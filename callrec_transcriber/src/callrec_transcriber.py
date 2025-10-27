#!/usr/bin/env python3
import os
import sys
import json
import time
import logging
import tempfile
import gearman
import boto3
import requests
from typing import Dict, Any, List, Optional
import re
import threading

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 1. Cofigurations ---
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)s %(name)s - %(message)s'
)
logger = logging.getLogger('callrec_transcriber')

_VALID_NAME = re.compile(r'^[A-Za-z0-9_.-]+$')


def safe_basename(name: str) -> str:
    """
    Validate and return a safe basename (no slashes, only allowed chars).
    Raises ValueError on invalid input.
    """
    if not name or not isinstance(name, str):
        raise ValueError("empty or non-string name")
    # disallow path-separators explicitly
    if '/' in name or '\\' in name:
        raise ValueError("invalid characters (path separators not allowed)")
    if not _VALID_NAME.match(name):
        raise ValueError("invalid characters in name (allowed: A-Za-z0-9_-. )")
    return name


# --- 2. Configuración General ---
STT_ENGINE = os.getenv('STT_ENGINE', 'local').lower()
TASK_NAME_STR = os.getenv('TASK_NAME', "tel-callrec-transcriber")
TASK_NAME = TASK_NAME_STR.encode()
GEARMAN_SERVER = os.getenv('GEARMAN_HOST', 'gearman:4730')
ASTERISK_MONITOR_PATH = os.getenv('ASTERISK_MONITOR_PATH', '/var/spool/asterisk/monitor')

# operational flags
DELETE_WAVS = os.getenv("DELETE_WAVS", "true").lower() == "true"
MAX_WAV_BYTES = int(os.getenv("MAX_WAV_BYTES", str(200 * 1024 * 1024)))
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT_SEC", "30"))

# ---------- Helpers: cache de transcribers, locks y respuestas job ----------
_transcriber_cache: Dict[str, "Transcriber"] = {}
_transcriber_locks: Dict[str, threading.Lock] = {}
_cache_creation_lock = threading.Lock()

_default_transcriber: Optional["Transcriber"] = None
_default_lock = threading.Lock()


def get_default_transcriber() -> "Transcriber":
    """
    Devuelve el transcriber por defecto (STT_ENGINE), creando y cacheándolo de forma lazy.
    """
    global _default_transcriber
    if _default_transcriber is None:
        with _default_lock:
            if _default_transcriber is None:
                tr, _ = get_cached_transcriber(STT_ENGINE)
                _default_transcriber = tr
    return _default_transcriber


def job_error(msg: str) -> bytes:
    return json.dumps({"status": "error", "message": msg}).encode('utf-8')


def job_success(results: List[Dict[str, str]]) -> bytes:
    return json.dumps({"status": "ok", "transcriptions": results}).encode('utf-8')


def get_cached_transcriber(engine: str):
    """
    Devuelve (transcriber, lock) para el engine, creándolos la primera vez.
    Seguro frente a concurrencia con _cache_creation_lock.
    """
    if engine in _transcriber_cache:
        return _transcriber_cache[engine], _transcriber_locks[engine]

    with _cache_creation_lock:
        if engine in _transcriber_cache:
            return _transcriber_cache[engine], _transcriber_locks[engine]
        # instanciar (puede lanzar)
        tr = get_transcriber(engine)
        lk = threading.Lock()
        _transcriber_cache[engine] = tr
        _transcriber_locks[engine] = lk
        return tr, lk


# --- 3. Abstracción del Transcriptor (Patrón Strategy) ---
class Transcriber:
    @property
    def requires_serialization(self) -> bool:
        """Override en transcribers que necesiten serialización (p.ej. modelos nativos)."""
        return False


# ---------- Motores ----------
class FasterWhisperTranscriber(Transcriber):
    def __init__(self):
        from faster_whisper import WhisperModel
        model_name = os.getenv("WHISPER_MODEL", "small")
        device = os.getenv("FASTER_WHISPER_DEVICE", "cpu")
        compute_type = os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "int8")
        logger.info(f"Cargando faster-whisper {model_name} ({device}, {compute_type})...")
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        # lock por instancia para serializar únicamente el uso del modelo nativo
        self._model_lock = threading.Lock()

    @property
    def requires_serialization(self) -> bool:
        return True

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        use_vad = os.getenv("FW_VAD_FILTER", "true").lower() == "true"
        kwargs = {"vad_filter": use_vad, "beam_size": 5}
        if language:
            kwargs["language"] = language
        # solo esta seccion usa el lock
        with self._model_lock:
            segments_iter, _ = self.model.transcribe(audio_path, **kwargs)

        segs: List[Dict[str, Any]] = []
        texts: List[str] = []
        for s in segments_iter:
            t = (s.text or "").strip()
            segs.append({"start": float(s.start), "end": float(s.end), "text": t})
            if t:
                texts.append(t)
        return {"text": " ".join(texts).strip(), "segments": segs}


class OpenAITranscriber(Transcriber):
    def __init__(self):
        self.api_key = os.getenv('STT_API_KEY')
        if not self.api_key:
            raise ValueError("Falta la variable de entorno STT_API_KEY para el motor 'openai'.")
        self.model = os.getenv('OPENAI_WHISPER_MODEL', 'whisper-1')
        self.api_url = "https://api.openai.com/v1/audio/transcriptions"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

        # Session con retry para errores transitorios
        self.session = requests.Session()
        retry_kwargs = dict(total=3, backoff_factor=1,
                            status_forcelist=[429, 500, 502, 503, 504])
        try:
            retries = Retry(**retry_kwargs, allowed_methods=["POST", "GET"])
        except TypeError:
            retries = Retry(**retry_kwargs, method_whitelist=["POST", "GET"])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        """
        Usa self.session.post con retries gestionados por urllib3/requests.
        Retorna dict {"text":..., "segments":[...]} o vacíos si falla.
        """
        try:
            with open(audio_path, 'rb') as audio_file:
                files = {'file': (os.path.basename(audio_path), audio_file, 'audio/wav')}
                data = {'model': self.model, 'response_format': 'verbose_json'}
                data['timestamp_granularities[]'] = 'segment'
                if language:
                    data['language'] = language

                response = self.session.post(
                    self.api_url,
                    headers=self.headers,
                    files=files,
                    data=data,
                    timeout=60
                )

            if response.status_code == 200:
                j = response.json()
                text = (j.get('text') or "").strip()
                segs_json = j.get('segments') or []
                segs = [{
                    "start": float(s.get('start', 0.0)),
                    "end": float(s.get('end', 0.0)),
                    "text": (s.get('text') or "").strip()
                } for s in segs_json if isinstance(s, dict)]
                return {"text": text, "segments": segs}

            # Errores 4xx (salvo 429) o cualquier otro código no 200
            logger.error(f"OpenAI API client error {response.status_code}: {response.text}")
            return {"text": "", "segments": []}

        except requests.RequestException as e:
            logger.error(f"OpenAITranscriber: request error or retries exhausted: {e}")
            return {"text": "", "segments": []}


class GeminiTranscriber(Transcriber):
    """Transcriptor que utiliza la API de Google Gemini (no ofrece timestamps robustos)."""
    def __init__(self):
        try:
            import google.generativeai as genai
            self.api_key = os.getenv('STT_API_KEY')
            if not self.api_key:
                raise ValueError("Falta la variable de entorno STT_API_KEY para el motor 'gemini'.")
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('models/gemini-1.5-flash')
            self.genai = genai
        except ImportError:
            logger.error("La biblioteca 'google-generativeai' no está instalada. Ejecuta: pip install google-generativeai")
            sys.exit(1)

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        prompt = "Transcribe este audio a texto."
        if language:
            prompt = f"Transcribe este audio a texto. El idioma es {language}."

        audio_file = None
        try:
            audio_file = self.genai.upload_file(path=audio_path)
            response = self.model.generate_content([prompt, audio_file])
            return {"text": (getattr(response, "text", "") or "").strip(), "segments": []}
        except Exception as e:
            logger.exception(f"Error durante la transcripción con Gemini: {e}")
            return {"text": "", "segments": []}
        finally:
            if audio_file:
                try:
                    self.genai.delete_file(audio_file.name)
                except Exception as e:
                    logger.warning(f"No se pudo eliminar el archivo temporal de Gemini '{audio_file.name}': {e}")


class GoogleCloudSpeechTranscriber(Transcriber):
    """Transcriptor que utiliza Google Cloud Speech-to-Text con timestamps por bloque."""
    def __init__(self):
        from google.cloud import speech_v1p1beta1 as speech
        self._speech = speech
        try:
            self.client = speech.SpeechClient()
        except Exception as e:
            logger.exception(f"No se pudo inicializar SpeechClient: {e}")
            raise

    def _lang_bcp47(self, lang: Optional[str]) -> str:
        if not lang:
            return "es-ES"
        if lang.lower() == "es":
            return "es-ES"
        return lang

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        speech = self._speech
        language_code = self._lang_bcp47(language)
        cfg = speech.RecognitionConfig(
            language_code=language_code,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
            model=os.getenv("GCP_SPEECH_MODEL", "phone_call"),  # "phone_call" | "default" | "latest_long"
            audio_channel_count=1,
        )

        with open(audio_path, "rb") as f:
            audio = speech.RecognitionAudio(content=f.read())

        try:
            op = self.client.long_running_recognize(config=cfg, audio=audio)
            resp = op.result(timeout=3600)
        except Exception as e:
            logger.exception(f"GCP Speech falló: {e}")
            return {"text": "", "segments": []}

        segs: List[Dict[str, Any]] = []
        texts: List[str] = []
        for r in resp.results:
            if not r.alternatives:
                continue
            alt = r.alternatives[0]
            t = (alt.transcript or "").strip()
            if t:
                texts.append(t)
            # Derivamos start/end por bloque usando primera/última palabra
            if getattr(alt, "words", None):
                w0 = alt.words[0]
                wN = alt.words[-1]
                start = w0.start_time.total_seconds() if w0.start_time else 0.0
                end = wN.end_time.total_seconds() if wN.end_time else start
                segs.append({"start": float(start), "end": float(end), "text": t})
            else:
                segs.append({"start": 0.0, "end": 0.0, "text": t})

        return {"text": " ".join(texts).strip(), "segments": segs}


def get_transcriber(engine: str) -> Transcriber:
    """Fábrica de transcriptores por engine."""
    logger.info(f"Inicializando motor de transcripción: {engine}")
    if engine == 'local':
        return FasterWhisperTranscriber()
    if engine == 'openai':
        return OpenAITranscriber()
    if engine == 'gemini':
        return GeminiTranscriber()
    if engine in ('gcp', 'google_speech', 'cloud_speech'):
        return GoogleCloudSpeechTranscriber()
    raise ValueError(f"Motor STT no soportado: '{engine}'. Opciones: local, openai, gemini, gcp.")


# --- 4. Inicialización de Clientes y Worker ---
# Cliente S3
s3_bucket = os.getenv('S3_BUCKET_NAME')
aws_key = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret = os.getenv('AWS_SECRET_ACCESS_KEY')
if not all([s3_bucket, aws_key, aws_secret]):
    logger.error('Faltan credenciales de S3: S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY')
    sys.exit(1)
s3 = boto3.client(
    's3',
    aws_access_key_id=aws_key,
    aws_secret_access_key=aws_secret,
    endpoint_url=os.getenv('S3_ENDPOINT', None),
    region_name=os.getenv('S3_REGION_NAME', None)
)


# --- 5. Lógica del Worker de Gearman ---
def process_single_wav(path: str, language: Optional[str], _transcriber: Optional[Transcriber] = None) -> Dict[str, Any]:
    """
    Procesa un WAV y lo transcribe con el motor seleccionado.

    Retorna: {"text": str, "segments": [{start, end, text}]}
    """
    try:
        if _transcriber is None:
            _transcriber = get_default_transcriber()

        engine = _transcriber
        result = engine.transcribe(path, language=language)

    except Exception as e:
        logger.exception(f"Falló la transcripción de {os.path.basename(path)}: {e}")
        result = {"text": "", "segments": []}

    return result


def task_process_audiofile(_, job):
    """Procesa un job de transcripción: genera .txt por canal y un JSON consolidado con timestamps."""

    try:
        payload = json.loads(job.data.decode('utf-8'))
        # required fields check
        required = ["fileName", "dateFileName"]
        missing = [k for k in required if not payload.get(k)]
        if missing:
            logger.error(f"Payload inválido. Faltan: {', '.join(missing)}")
            return job_error('Missing fields')

        # sanitize and validate fileName and dateFileName to prevent path traversal
        try:
            base_name_raw = payload.get('fileName')
            date_folder_raw = payload.get('dateFileName')
            base_name = safe_basename(base_name_raw)
            date_folder = safe_basename(date_folder_raw)
        except ValueError as e:
            logger.error(f"Invalid payload fileName/dateFileName: {e}")
            return job_error('Invalid payload')

        # Construct canonical folder_path and ensure it's inside ASTERISK_MONITOR_PATH
        folder_path = os.path.normpath(os.path.join(ASTERISK_MONITOR_PATH, date_folder))
        base_monitor_norm = os.path.normpath(ASTERISK_MONITOR_PATH)
        # Ensure folder_path is strictly under ASTERISK_MONITOR_PATH (avoid ../ tricks)
        if not (folder_path + os.sep).startswith(base_monitor_norm + os.sep):
            logger.error("Payload dateFileName points outside monitor path")
            return job_error('Invalid payload')

        engine_override = (payload.get('engine') or "").lower().strip()
        language = payload.get('language', None)

        # resolver transcriber (usar cache)
        if engine_override and engine_override != STT_ENGINE:
            effective_engine = engine_override
        else:
            effective_engine = STT_ENGINE

        try:
            current_transcriber, engine_lock = get_cached_transcriber(effective_engine)
        except Exception as e:
            logger.error(f"Engine '{effective_engine}' no disponible: {e}")
            return job_error('Unavailable engine')

        if not base_name or not date_folder:
            logger.error('Payload inválido: faltan fileName o dateFileName')
            return job_error('Missing fields')

        # --- Espera y reintentos para carpeta y archivos (evita race conditions)
        FOLDER_WAIT_SEC = int(os.getenv("FOLDER_WAIT_SEC", "30"))        # tiempo máximo a esperar por la carpeta
        FOLDER_POLL_INTERVAL = float(os.getenv("FOLDER_POLL_INTERVAL", "0.5"))
        FILE_WAIT_SEC = int(os.getenv("FILE_WAIT_SEC", "30"))            # tiempo máximo a esperar por los archivos wav
        FILE_POLL_INTERVAL = float(os.getenv("FILE_POLL_INTERVAL", "0.5"))

        # 1) Esperar carpeta
        waited = 0.0
        while not os.path.isdir(folder_path) and waited < FOLDER_WAIT_SEC:
            logger.debug(f"Folder {folder_path} not found, waiting {FOLDER_POLL_INTERVAL}s... ({waited:.1f}/{FOLDER_WAIT_SEC}s)")
            time.sleep(FOLDER_POLL_INTERVAL)
            waited += FOLDER_POLL_INTERVAL

        if not os.path.isdir(folder_path):
            logger.error(f'La carpeta de grabaciones no existe (timeout): {folder_path}')
            return job_error('Folder not found')

        # 2) Comprobar que ambos wav existan y tengan tamaño > 0 (esperar un tiempo)
        wav_files = [f"{base_name}-Rx.wav", f"{base_name}-Tx.wav"]
        start_time = time.time()
        while True:
            present = []
            for wav in wav_files:
                p = os.path.join(folder_path, wav)
                if os.path.isfile(p):
                    try:
                        if os.path.getsize(p) > 0:
                            present.append(wav)
                        else:
                            logger.debug(f"{p} exists but size 0, waiting")
                    except OSError:
                        logger.debug(f"Could not stat {p}, waiting")
                else:
                    logger.debug(f"{p} not present yet")
            if set(present) == set(wav_files):
                break
            if time.time() - start_time > FILE_WAIT_SEC:
                logger.error(f"WAV files not ready in {FILE_WAIT_SEC}s: missing or zero-size in {folder_path}")
                # Decide: skip job or return error. Mejor devolver error para que productor reintente.
                return job_error('WAV files not ready')
            time.sleep(FILE_POLL_INTERVAL)

        wav_files = [f"{base_name}-Rx.wav", f"{base_name}-Tx.wav"]
        transcription_results = []
        all_segments: List[Dict[str, Any]] = []
        text_by_channel = {"Rx": "", "Tx": ""}

        for wav_filename in wav_files:
            full_path = os.path.join(folder_path, wav_filename)
            if not os.path.isfile(full_path):
                logger.warning(f'Archivo WAV no encontrado, se omite: {full_path}')
                continue

            # Transcribir (con timestamps si el engine lo soporta)
            # Chequeo de tamaño antes de procesar
            try:
                size = os.path.getsize(full_path)
                if size > MAX_WAV_BYTES:
                    logger.error(f"WAV demasiado grande ({size} bytes), se omite: {full_path}")
                    continue
            except OSError:
                logger.warning(f"No se pudo obtener tamaño de archivo de {full_path}; procediendo.")

            # Ejecutar la transcripción bajo el lock del engine (serializa llamadas a native libs)
            if getattr(current_transcriber, "requires_serialization", False):
                with engine_lock:
                    result = process_single_wav(full_path, language, _transcriber=current_transcriber)
            else:
                # APIs remotas no necesitan serialización a nivel de proceso local
                result = process_single_wav(full_path, language, _transcriber=current_transcriber)

            text = result.get("text", "") or ""
            segments = result.get("segments", []) or []

            # Canal por sufijo del nombre
            channel = "Rx" if wav_filename.endswith("-Rx.wav") else "Tx"
            text_by_channel[channel] = text

            # Adjuntar canal a cada segmento
            for s in segments:
                s["channel"] = channel
            all_segments.extend(segments)

            # Subir .txt por compatibilidad
            if text:
                fd_txt, tmp_txt_path = tempfile.mkstemp(suffix=".txt")
                os.close(fd_txt)
                try:
                    with open(tmp_txt_path, 'w', encoding='utf-8') as f:
                        f.write(text)
                    s3_key = f"{date_folder}/{os.path.splitext(wav_filename)[0]}.txt"
                    s3.upload_file(tmp_txt_path, s3_bucket, s3_key)
                    logger.info(f"Transcripción subida exitosamente a S3: s3://{s3_bucket}/{s3_key}")
                    transcription_results.append({'file': wav_filename, 's3_key': s3_key})
                except Exception as e:
                    logger.exception(f"Error al subir el archivo de transcripción a S3: {e}")
                finally:
                    try:
                        os.remove(tmp_txt_path)
                    except OSError:
                        pass

        # Ordenar los segmentos por tiempo (primero start, luego canal por estabilidad)
        all_segments.sort(key=lambda x: (x.get("start", 0.0), x.get("channel", "")))

        logger.info(f"Segmentos totales: {len(all_segments)} "
            f"(Rx: {sum(1 for s in all_segments if s.get('channel')=='Rx')}, "
            f"Tx: {sum(1 for s in all_segments if s.get('channel')=='Tx')})")
    
        # Construir JSON maestro
        convo_json = {
            "base_name": base_name,
            "date_folder": date_folder,
            "engine": effective_engine,
            "language": language,
            "segments": all_segments,       # [{start, end, text, channel}, ...]
            "text_by_channel": text_by_channel
        }

        # Subir JSON consolidado
        fd_json, tmp_json_path = tempfile.mkstemp(suffix=".json")
        os.close(fd_json)
        try:
            with open(tmp_json_path, 'w', encoding='utf-8') as jf:
                json.dump(convo_json, jf, ensure_ascii=False, indent=2)
            json_key = f"{date_folder}/{base_name}.json"
            s3.upload_file(tmp_json_path, s3_bucket, json_key)
            logger.info(f"JSON con timestamps subido a S3: s3://{s3_bucket}/{json_key}")
            transcription_results.append({'file': f"{base_name}.json", 's3_key': json_key})
        except Exception as e:
            logger.exception(f"Error al subir JSON con timestamps a S3: {e}")
        finally:
            try:
                os.remove(tmp_json_path)
            except OSError:
                pass

        # Limpiar WAV originales después de procesarlos (si está habilitado)
        if DELETE_WAVS:
            for wav_filename in wav_files:
                try:
                    os.remove(os.path.join(folder_path, wav_filename))
                except OSError:
                    pass

        return job_success(transcription_results)

    except Exception as e:
        logger.exception(f'Error irrecuperable al procesar el job: {e}')
        return job_error('Fail')


def initialize_default_engine(engine: str):
    """
    Inicializa y cachea el transcriber indicado.
    Si engine == 'local' valida HF_HOME (crea si es necesario y verifica permisos).
    Lanza excepción si falla (fail-fast).
    """
    hf_home = os.getenv("HF_HOME", "/opt/models")
    if engine == "local":
        try:
            os.makedirs(hf_home, exist_ok=True)
            # Verificar escritura (testfile)
            testfile = os.path.join(hf_home, ".permtest")
            with open(testfile, "w") as f:
                f.write("ok")
            os.remove(testfile)
        except Exception as e:
            logger.exception(f"HF_HOME ({hf_home}) not writable or initializable: {e}")
            raise

    # Crea/cacha el transcriber y su lock
    tr, lk = get_cached_transcriber(engine)
    with _default_lock:
        global _default_transcriber
        if _default_transcriber is None:
            _default_transcriber = tr
    return tr, lk


# --- 6. Arranque del Worker ---
if __name__ == "__main__":
    # --- EAGER LOAD: inicializar SOLO el engine configurado por STT_ENGINE ---
    try:
        logger.info(f"EAGER LOAD: initializing configured STT_ENGINE='{STT_ENGINE}' at startup...")
        initialize_default_engine(STT_ENGINE)
        logger.info(f"EAGER LOAD: STT_ENGINE='{STT_ENGINE}' initialized and cached.")
    except Exception as e:
        logger.exception(f"EAGER LOAD: failed to initialize configured STT_ENGINE='{STT_ENGINE}': {e}")
        # Fail-fast: no tiene sentido aceptar jobs sin transcriber disponible
        sys.exit(1)

    # --- Arranque del Worker ---
    gm_worker = gearman.GearmanWorker([GEARMAN_SERVER])
    gm_worker.register_task(TASK_NAME, task_process_audiofile)  # TASK_NAME es bytes
    logger.info(f"Worker de Gearman listo para recibir tareas en '{TASK_NAME_STR}'...")
    try:
        gm_worker.work()
    except gearman.errors.ServerUnavailable:
        logger.error(f'No se pudo conectar al servidor de Gearman en {GEARMAN_SERVER}')
        sys.exit(1)
