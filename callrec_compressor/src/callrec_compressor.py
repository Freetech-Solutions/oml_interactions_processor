# worker/worker.py
import os
import json
import logging
import subprocess
import tempfile
import gearman
import boto3
from datetime import datetime
from botocore.exceptions import NoCredentialsError, ClientError

# ───── Configuración general ─────
ASTERISK_MONITOR_PATH = os.getenv('ACD_REC_PATH', '/var/spool/asterisk/recording/')
GEARMAN_SERVER = os.getenv('GEARMAN_HOST', 'gearman:4730')
TASK_NAME = b'tel-callrec-compressor'

# ───── Logging ─────
logger = logging.getLogger("callrec_worker")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)

# ───── Configuración S3 Universal ─────
s3_bucket_name = os.getenv("S3_BUCKET_NAME")
aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
endpoint_url = os.getenv("S3_ENDPOINT", None)
region_name = os.getenv("S3_REGION_NAME", 'us-east-1')
storage_type = os.getenv('CALLREC_DEVICE', 's3-aws')

try:
    s3 = boto3.client(
        's3',
        region_name=region_name if storage_type == 's3-aws' else None,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        endpoint_url=endpoint_url if storage_type != 's3-aws' else None,
        verify=(storage_type != 's3-no-check-cert')
    )
    logger.info(f"Cliente S3 inicializado. Tipo: {storage_type}, Endpoint: {endpoint_url}, Region: {region_name}")
except Exception as e:
    logger.exception("Fallo crítico al inicializar cliente S3.")
    exit(1)

gm_worker = gearman.GearmanWorker([GEARMAN_SERVER])

# ───── Funciones Auxiliares (Helpers) ─────

def upload_to_s3(source_path, destination_path, metadata):
    try:
        clean_metadata = {k: str(v) for k, v in metadata.items() if v is not None}
        
        extra_args = {
            'Metadata': clean_metadata,
            # ContentType y ACL eliminados para compatibilidad con buckets cloud
        }

        s3.upload_file(
            source_path, 
            s3_bucket_name, 
            destination_path,
            ExtraArgs=extra_args
        )
        logger.info(f"☁️ Subido a S3: {destination_path} | Metadata: {clean_metadata}")
        return True
    except (NoCredentialsError, ClientError) as e:
        logger.exception(f"Fallo en subida a S3 de {source_path}")
        return False

def convert_to_mp3(source_path, mp3_path):
    try:
        cmd = ['ffmpeg', '-y', '-i', source_path, '-codec:a', 'libmp3lame', '-qscale:a', '4', '-nostats', '-loglevel', 'error', mp3_path]
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        logger.error(f"Fallo en conversión MP3 de {source_path}")
        return False


def download_from_s3(bucket, s3_key, local_path):
    """Descarga un objeto desde S3 a un path local. Retorna True si OK."""
    try:
        s3.download_file(bucket, s3_key, local_path)
        logger.info(f"Descargado desde S3: s3://{bucket}/{s3_key} -> {local_path}")
        return True
    except (NoCredentialsError, ClientError, OSError) as e:
        logger.exception(f"Fallo descargando s3://{bucket}/{s3_key}: {e}")
        return False


def delete_from_s3(bucket, s3_key):
    """Elimina un objeto desde S3. Retorna True si OK."""
    try:
        s3.delete_object(Bucket=bucket, Key=s3_key)
        logger.info(f"Eliminado desde S3: s3://{bucket}/{s3_key}")
        return True
    except (NoCredentialsError, ClientError) as e:
        logger.exception(f"Fallo eliminando s3://{bucket}/{s3_key}: {e}")
        return False


# ───── Lógica Principal del Worker ─────

def task_process_audiofile(gearman_worker, gearman_job):
    """
    Worker de compresión: convierte WAV a MP3 y sube a S3.
    Si el job tiene s3_wav_key, descarga el WAV desde S3; si no, lo lee desde disco local (legacy).
    La persistencia de `archivo_grabacion` en la base de datos la realiza el logger ACD.
    """
    try:
        data = json.loads(gearman_job.data.decode('utf-8'))

        filename_base = data.get('fileName')
        date_dialplan = data.get('dateFileName')
        call_metadata = data.get('metadata', {})
        s3_wav_key = data.get('s3_wav_key')
        job_bucket = data.get('s3_bucket_name') or s3_bucket_name

        if not filename_base:
            logger.error("Job recibido sin fileName")
            return b'Fail'

        mp3_file = f"{filename_base}.mp3"

        # Convertir fecha de YYYYMMDD a YYYY-MM-DD para el path S3 del MP3
        try:
            if date_dialplan and len(date_dialplan) == 8:
                date_obj = datetime.strptime(date_dialplan, '%Y%m%d')
                date_folder = date_obj.strftime('%Y-%m-%d')
            else:
                date_folder = datetime.now().strftime('%Y-%m-%d')
                logger.warning(f"⚠️ Formato de fecha inesperado: {date_dialplan}. Usando fecha actual: {date_folder}")
        except Exception as e:
            date_folder = datetime.now().strftime('%Y-%m-%d')
            logger.warning(f"⚠️ Error al convertir fecha {date_dialplan}: {e}. Usando fecha actual: {date_folder}")

        s3_key_path = f"{date_folder}/{mp3_file}"

        use_s3_source = bool(s3_wav_key)
        source_path = None
        local_mp3_path = None
        temp_files = []

        if use_s3_source:
            # Flujo S3: descargar WAV desde S3 a temp, convertir, subir MP3, borrar temp
            fd_wav, source_path = tempfile.mkstemp(suffix='.wav')
            os.close(fd_wav)
            temp_files.append(source_path)
            if not download_from_s3(job_bucket, s3_wav_key, source_path):
                for p in temp_files:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                return b'S3 Download Fail'
            fd_mp3, local_mp3_path = tempfile.mkstemp(suffix='.mp3')
            os.close(fd_mp3)
            temp_files.append(local_mp3_path)
            logger.info(f"🔄 Procesando WAV desde S3 ({s3_wav_key}) hacia MP3 en S3: {s3_key_path}")
        else:
            # Flujo legacy: leer WAV desde disco local (ASTERISK_MONITOR_PATH)
            source_file = f"{filename_base}.wav"
            source_path = os.path.join(ASTERISK_MONITOR_PATH, source_file)
            local_mp3_path = os.path.join(ASTERISK_MONITOR_PATH, mp3_file)
            if not os.path.exists(source_path):
                logger.warning(f"Archivo WAV no encontrado en ruta plana: {source_path}")
                return b'File not found'
            logger.info(f"🔄 Procesando desde: {source_path} hacia S3: {s3_key_path}")

        # 1. Convertir
        if not convert_to_mp3(source_path, local_mp3_path):
            if use_s3_source:
                for p in temp_files:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            return b'Conversion Fail'

        # 2. Subir MP3 a S3
        if not upload_to_s3(local_mp3_path, s3_key_path, call_metadata):
            if use_s3_source:
                for p in temp_files:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            return b'S3 Upload Fail'

        # 2b. Si el WAV estaba en S3, eliminarlo del bucket tras subida exitosa del MP3
        if use_s3_source:
            if not delete_from_s3(job_bucket, s3_wav_key):
                logger.warning(f"No se pudo eliminar el WAV en S3: s3://{job_bucket}/{s3_wav_key}")

        # 3. Limpiar archivos locales
        try:
            os.remove(local_mp3_path)
            if use_s3_source:
                os.remove(source_path)
                logger.info("🗑️ Archivos temporales eliminados.")
            else:
                os.remove(source_path)
                logger.info("🗑️ Archivos locales eliminados.")
        except OSError as e:
            logger.warning(f"No se pudieron borrar archivos locales: {e}")

        return b'Ok'

    except Exception as e:
        logger.exception(f"Error no controlado en el worker")
        return b'Fail'

# ───── Loop de Espera ─────

try:
    gm_worker.register_task(TASK_NAME, task_process_audiofile)
    logger.info(f"Worker conectado a {GEARMAN_SERVER}. Esperando jobs...")
    gm_worker.work()
except Exception as e:
    logger.critical(f"Error fatal en el worker: {e}")