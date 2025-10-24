#!/usr/bin/env python3
import os
import sys
import json
import time
import logging
import tempfile
import subprocess
import gearman
import boto3
import requests
import re
from typing import Dict, Any, List, Optional

_VALID_NAME = re.compile(r'^[\w\-\.]+$')

# --- 1. Configuración de Logging ---
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)s %(name)s - %(message)s'
)
logger = logging.getLogger('callrec_transcriber')

# --- 2. Configuración General ---
STT_ENGINE = os.getenv('STT_ENGINE', 'local').lower()
TASK_NAME = os.getenv('TASK_NAME', "tel-callrec-transcriber").encode()
GEARMAN_SERVER = os.getenv('GEARMAN_HOST', 'gearman:4730')
ASTERISK_MONITOR_PATH = os.getenv('ASTERISK_MONITOR_PATH', '/var/spool/asterisk/monitor')


# --- 3. Abstracción del Transcriptor (Patrón Strategy) ---
class Transcriber:
    """Clase base para todos los motores de transcripción."""
    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        """
        Debe devolver:
        {
          "text": str,
          "segments": List[{"start": float, "end": float, "text": str}]
        }
        """
        raise NotImplementedError("Cada motor debe implementar 'transcribe'.")


# ---------- Motores ----------
class FasterWhisperTranscriber(Transcriber):
    """Transcriptor local con faster-whisper (CTranslate2)."""
    def __init__(self):
        from faster_whisper import WhisperModel  # import perezoso
        model_name = os.getenv("WHISPER_MODEL", "small")
        device = os.getenv("FASTER_WHISPER_DEVICE", "cpu")  # "cpu" | "cuda"
        compute_type = os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "int8")
        logger.info(f"Cargando faster-whisper {model_name} ({device}, {compute_type})...")
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        use_vad = os.getenv("FW_VAD_FILTER", "true").lower() == "true"
        kwargs = {"vad_filter": use_vad, "beam_size": 5}
        if language:
            kwargs["language"] = language
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
    """Transcriptor que utiliza la API de OpenAI Whisper (SaaS)."""
    def __init__(self):
        self.api_key = os.getenv('STT_API_KEY')
        if not self.api_key:
            raise ValueError("Falta la variable de entorno STT_API_KEY para el motor 'openai'.")
        self.model = os.getenv('OPENAI_WHISPER_MODEL', 'whisper-1')
        self.api_url = "https://api.openai.com/v1/audio/transcriptions"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        for attempt in range(3):
            try:
                with open(audio_path, 'rb') as audio_file:
                    files = {'file': (os.path.basename(audio_path), audio_file, 'audio/wav')}
                    # Pedimos verbose_json para obtener segments
                    data = {'model': self.model, 'response_format': 'verbose_json'}
                    # granularity segment (si el endpoint lo soporta)
                    data['timestamp_granularities[]'] = 'segment'
                    if language:
                        data['language'] = language

                    response = requests.post(
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

                logger.error(f"Error en API de OpenAI {response.status_code}: {response.text}")
                break
            except requests.RequestException as e:
                logger.warning(f"OpenAI POST intento {attempt+1}/3 falló: {e}")
                time.sleep(1 + attempt)
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

# Instancia (default) del transcriptor seleccionado al arrancar
_default_transcriber = get_transcriber(STT_ENGINE)


# --- 5. Lógica del Worker de Gearman ---
def process_single_wav(path: str, language: Optional[str], _transcriber: Optional[Transcriber] = None) -> Dict[str, Any]:
    """
    Procesa un WAV: opcionalmente recorta silencios y luego transcribe con el motor seleccionado.

    Retorna: {"text": str, "segments": [{start, end, text}]}

    Env vars:
      - USE_FFMPEG_TRIM=true|false   (default: false)
      - TRIM_STOP_DURATION=0.5
      - TRIM_THRESHOLD_DB=-50
    """
    use_trim = os.getenv("USE_FFMPEG_TRIM", "false").lower() == "true"

    # Valores de trim (tunear por env)
    try:
        trim_stop_duration = float(os.getenv("TRIM_STOP_DURATION", "0.5"))
    except ValueError:
        trim_stop_duration = 0.5
    try:
        trim_threshold_db = float(os.getenv("TRIM_THRESHOLD_DB", "-50"))
    except ValueError:
        trim_threshold_db = -50.0

    audio_src_path = path
    trimmed_path = None

    # 1) Recorte opcional con ffmpeg
    if use_trim:
        fd_trim, trimmed_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_trim)
        ffmpeg_filter = (
            f"silenceremove=stop_periods=-1:"
            f"stop_duration={trim_stop_duration}:"
            f"stop_threshold={trim_threshold_db}dB"
        )
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-af", ffmpeg_filter, trimmed_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            audio_src_path = trimmed_path
            logger.debug(
                f"Silence trim aplicado ({os.path.basename(path)} -> {os.path.basename(trimmed_path)} | "
                f"dur={trim_stop_duration}s, thr={trim_threshold_db}dB)"
            )
        except Exception as e:
            logger.warning(
                f"No se pudo recortar el silencio de {os.path.basename(path)}; "
                f"se usará el original. Detalle: {e}"
            )
            audio_src_path = path

    # 2) Transcribir usando el motor seleccionado (inyectado o default)
    try:
        engine = _transcriber or _default_transcriber
        result = engine.transcribe(audio_src_path, language=language)
        # result: {"text": str, "segments": [...]}
    except Exception as e:
        logger.exception(f"Falló la transcripción de {os.path.basename(path)}: {e}")
        result = {"text": "", "segments": []}
    finally:
        # 3) Limpieza: borrar el archivo temporal si lo creamos
        if trimmed_path:
            try:
                os.remove(trimmed_path)
            except OSError:
                pass

    return result


def task_process_audiofile(_, job):
    """Procesa un job de transcripción: genera .txt por canal y un JSON consolidado con timestamps."""

    try:
        payload = json.loads(job.data.decode('utf-8'))
        required = ["fileName", "dateFileName"]
        missing = [k for k in required if not payload.get(k)]
        if missing:
            logger.error(f"Payload inválido. Faltan: {', '.join(missing)}")
            return b'Missing fields'
        
        engine_override = (payload.get('engine') or "").lower().strip()
        language = payload.get('language', None)
        base_name = payload.get('fileName')
        date_folder = payload.get('dateFileName')

        # Resolver transcriber a usar en ESTE job sin mutar el default
        current_transcriber = _default_transcriber
        effective_engine = STT_ENGINE
        if engine_override and engine_override != STT_ENGINE:
            try:
                logger.info(f"Override de motor: {STT_ENGINE} -> {engine_override}")
                current_transcriber = get_transcriber(engine_override)
                effective_engine = engine_override
            except Exception as e:
                logger.error(f"Engine '{engine_override}' no disponible: {e}")
                return b'Unavailable engine'

        if not base_name or not date_folder:
            logger.error('Payload inválido: faltan fileName o dateFileName')
            return b'Missing fields'

        folder_path = os.path.join(ASTERISK_MONITOR_PATH, date_folder)
        if not os.path.isdir(folder_path):
            logger.error(f'La carpeta de grabaciones no existe: {folder_path}')
            return b'Folder not found'

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

        # Limpiar WAV originales después de procesarlos
        for wav_filename in wav_files:
            try:
                os.remove(os.path.join(folder_path, wav_filename))
            except OSError:
                pass

        return json.dumps({'transcriptions': transcription_results}).encode('utf-8')

    except Exception as e:
        logger.exception(f'Error irrecuperable al procesar el job: {e}')
        return b'Fail'


# --- 6. Arranque del Worker ---
if __name__ == "__main__":
    gm_worker = gearman.GearmanWorker([GEARMAN_SERVER])
    gm_worker.register_task(TASK_NAME, task_process_audiofile)
    logger.info(f"Worker de Gearman listo para recibir tareas en '{TASK_NAME.decode()}'...")
    try:
        gm_worker.work()
    except gearman.errors.ServerUnavailable:
        logger.error(f'No se pudo conectar al servidor de Gearman en {GEARMAN_SERVER}')
        sys.exit(1)
