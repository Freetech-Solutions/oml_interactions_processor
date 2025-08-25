#!/usr/bin/env python3
import os
import sys
import json
import logging
import tempfile
import subprocess
import gearman
import boto3
import requests

# --- 1. Configuración de Logging ---
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)s %(name)s - %(message)s'
)
logger = logging.getLogger('callrec_transcriber')

# --- 2. Configuración General ---
STT_ENGINE = os.getenv('STT_ENGINE', 'local').lower()
GEARMAN_SERVER = os.getenv('GEARMAN_HOST', 'gearman:4730')
TASK_NAME = os.getenv('TASK_NAME', 'tel-callrec-transcriber').encode()
ASTERISK_MONITOR_PATH = os.getenv('ASTERISK_MONITOR_PATH', '/var/spool/asterisk/monitor')

# --- 3. Abstracción del Transcriptor (Patrón Strategy) ---


class Transcriber:
    """Clase base abstracta para todos los motores de transcripción."""
    def transcribe(self, audio_path: str, language: str = None) -> str:
        """
        Toma la ruta de un archivo de audio y devuelve el texto transcrito.
        Este método debe ser implementado por cada subclase.
        """
        raise NotImplementedError("Cada motor debe implementar el método 'transcribe'.")


class LocalWhisperTranscriber(Transcriber):
    """Transcriptor que utiliza un modelo de Whisper ejecutado localmente."""
    def __init__(self):
        try:
            import whisper
            model_name = os.getenv('WHISPER_MODEL', 'base')
            logger.info(f"Cargando modelo local de Whisper '{model_name}'...")
            self.model = whisper.load_model(model_name)
            logger.info("Modelo Whisper local cargado exitosamente.")
        except ImportError:
            logger.error("La biblioteca 'openai-whisper' no está instalada. Ejecuta: pip install openai-whisper")
            sys.exit(1)
        except Exception as e:
            logger.exception(f"Error al cargar el modelo Whisper: {e}")
            sys.exit(1)

    def transcribe(self, audio_path: str, language: str = None) -> str:
        options = {'language': language} if language else {}
        result = self.model.transcribe(audio_path, **options)
        return result.get('text', '').strip()


class OpenAITranscriber(Transcriber):
    """Transcriptor que utiliza la API de OpenAI Whisper."""
    def __init__(self):
        self.api_key = os.getenv('STT_API_KEY')
        if not self.api_key:
            # CORREGIDO: Mensaje de error actualizado para STT_API_KEY
            raise ValueError("Falta la variable de entorno STT_API_KEY para el motor 'openai'.")
        self.model = os.getenv('OPENAI_WHISPER_MODEL', 'whisper-1')
        self.api_url = "https://api.openai.com/v1/audio/transcriptions"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def transcribe(self, audio_path: str, language: str = None) -> str:
        with open(audio_path, 'rb') as audio_file:
            files = {'file': (os.path.basename(audio_path), audio_file, 'audio/wav')}
            data = {'model': self.model}
            if language:
                data['language'] = language

            response = requests.post(self.api_url, headers=self.headers, files=files, data=data)

            if response.status_code == 200:
                return response.json().get('text', '').strip()

            logger.error(f"Error en API de OpenAI {response.status_code}: {response.text}")
            return ""


class GeminiTranscriber(Transcriber):
    """Transcriptor que utiliza la API de Google Gemini."""
    def __init__(self):
        try:
            # CORREGIDO: La forma correcta de importar la biblioteca
            import google.generativeai as genai
            self.api_key = os.getenv('STT_API_KEY')
            if not self.api_key:
                # CORREGIDO: Mensaje de error actualizado y sin typos
                raise ValueError("Falta la variable de entorno STT_API_KEY para el motor 'gemini'.")
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('models/gemini-1.5-flash')
            # Guardamos la referencia para usarla en el método transcribe
            self.genai = genai
        except ImportError:
            logger.error("La biblioteca 'google-generativeai' no está instalada. Ejecuta: pip install google-generativeai")
            sys.exit(1)

    def transcribe(self, audio_path: str, language: str = None) -> str:
        prompt = "Transcribe este audio a texto."
        if language:
            prompt = f"Transcribe este audio a texto. El idioma es {language}."

        audio_file = None
        try:
            # Sube el archivo a la API de Gemini
            audio_file = self.genai.upload_file(path=audio_path)
            # Genera la transcripción
            response = self.model.generate_content([prompt, audio_file])
            return response.text.strip()
        except Exception as e:
            logger.exception(f"Error durante la transcripción con Gemini: {e}")
            return ""
        finally:
            # Es crucial eliminar el archivo subido para no agotar el almacenamiento de la API
            if audio_file:
                try:
                    self.genai.delete_file(audio_file.name)
                except Exception as e:
                    logger.warning(f"No se pudo eliminar el archivo temporal de Gemini '{audio_file.name}': {e}")


def get_transcriber(engine: str) -> Transcriber:
    """Fábrica que devuelve una instancia del transcriptor solicitado."""
    logger.info(f"Inicializando motor de transcripción: {engine}")
    try:
        if engine == 'local':
            return LocalWhisperTranscriber()
        elif engine == 'openai':
            return OpenAITranscriber()
        elif engine == 'gemini':
            return GeminiTranscriber()
        else:
            raise ValueError(f"Motor STT no soportado: '{engine}'. Opciones: local, openai, gemini.")
    except (ValueError, ImportError) as e:
        logger.error(f"Error fatal al inicializar el motor '{engine}': {e}")
        sys.exit(1)


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

# Instancia del transcriptor seleccionado (se crea una sola vez)
transcriber = get_transcriber(STT_ENGINE)

# --- 5. Lógica del Worker de Gearman ---


def process_single_wav(path: str, language: str) -> str:
    """Función auxiliar que procesa un único archivo WAV: recorta silencio y transcribe."""
    text = ""
    # Recortar silencio para optimizar tokens/procesamiento
    fd_trim, trimmed_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd_trim)
    try:
        # Ejecuta ffmpeg sin mostrar su salida para no ensuciar los logs
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", path,
                "-af", "silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-50dB",
                trimmed_path
            ], 
            check=True, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        audio_src_path = trimmed_path
    except Exception:
        logger.warning(f"No se pudo recortar el silencio de {os.path.basename(path)}, se usará el archivo original.")
        audio_src_path = path

    # Transcribir usando el motor seleccionado
    try:
        text = transcriber.transcribe(audio_src_path, language=language)
    except Exception as e:
        logger.exception(f"Falló la transcripción de {os.path.basename(path)}: {e}")
    finally:
        # Limpiar el archivo temporal si fue creado
        if audio_src_path == trimmed_path:
            try:
                os.remove(trimmed_path)
            except OSError:
                pass
    return text


def task_process_audiofile(_, job):
    """Función principal que procesa el trabajo de Gearman."""
    try:
        payload = json.loads(job.data.decode('utf-8'))
        base_name = payload.get('fileName')
        date_folder = payload.get('dateFileName')
        language = payload.get('language', None)

        if not base_name or not date_folder:
            logger.error('Payload inválido: faltan fileName o dateFileName')
            return b'Missing fields'

        folder_path = os.path.join(ASTERISK_MONITOR_PATH, date_folder)
        if not os.path.isdir(folder_path):
            logger.error(f'La carpeta de grabaciones no existe: {folder_path}')
            return b'Folder not found'

        wav_files = [f"{base_name}-Rx.wav", f"{base_name}-Tx.wav"]
        transcription_results = []

        for wav_filename in wav_files:
            full_path = os.path.join(folder_path, wav_filename)
            if not os.path.isfile(full_path):
                logger.warning(f'Archivo WAV no encontrado, se omite: {full_path}')
                continue

            # La lógica de procesamiento está ahora en una función auxiliar
            text = process_single_wav(full_path, language)

            if not text:
                logger.warning(f"No se obtuvo transcripción para {wav_filename}, se omite la subida a S3.")
                continue

            # Guardar en .txt temporal y subir a S3
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

        # Limpiar los archivos WAV originales después de procesarlos
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
