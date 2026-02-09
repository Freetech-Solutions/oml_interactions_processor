# worker/worker.py
import os
import json
import logging
import subprocess
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

# ───── Lógica Principal del Worker ─────

def task_process_audiofile(gearman_worker, gearman_job):
    """
    Worker de compresión: se limita a convertir el audio y subirlo a S3.
    La persistencia de `archivo_grabacion` en la base de datos la realiza el logger ACD.
    """
    try:
        data = json.loads(gearman_job.data.decode('utf-8'))
        
        filename_base = data.get('fileName')
        date_dialplan = data.get('dateFileName')
        call_metadata = data.get('metadata', {})

        if not filename_base:
            logger.error("Job recibido sin fileName")
            return b'Fail'

        source_file = f"{filename_base}.wav"
        mp3_file = f"{filename_base}.mp3"
        
        # Rutas locales (Planos)
        source_path = os.path.join(ASTERISK_MONITOR_PATH, source_file)
        local_mp3_path = os.path.join(ASTERISK_MONITOR_PATH, mp3_file)
        
        # Convertir fecha de YYYYMMDD a YYYY-MM-DD para el path S3
        try:
            if date_dialplan and len(date_dialplan) == 8:
                # Formato recibido: YYYYMMDD
                date_obj = datetime.strptime(date_dialplan, '%Y%m%d')
                date_folder = date_obj.strftime('%Y-%m-%d')
            else:
                # Si no tiene el formato esperado, usar la fecha actual
                date_folder = datetime.now().strftime('%Y-%m-%d')
                logger.warning(f"⚠️ Formato de fecha inesperado: {date_dialplan}. Usando fecha actual: {date_folder}")
        except Exception as e:
            # En caso de error, usar la fecha actual
            date_folder = datetime.now().strftime('%Y-%m-%d')
            logger.warning(f"⚠️ Error al convertir fecha {date_dialplan}: {e}. Usando fecha actual: {date_folder}")
        
        # Ruta S3 (Jerárquica con formato YYYY-MM-DD)
        s3_key_path = f"{date_folder}/{mp3_file}"

        if not os.path.exists(source_path):
            logger.warning(f"Archivo WAV no encontrado en ruta plana: {source_path}")
            return b'File not found'

        logger.info(f"🔄 Procesando desde: {source_path} hacia S3: {s3_key_path}")

        # 1. Convertir
        if convert_to_mp3(source_path, local_mp3_path):
            # 2. Subir
            if upload_to_s3(local_mp3_path, s3_key_path, call_metadata):
                # 3. Limpiar archivos locales (este worker no toca la base de datos)
                try:
                    os.remove(local_mp3_path)
                    os.remove(source_path)
                    logger.info("🗑️ Archivos locales eliminados.")
                except OSError as e:
                    logger.warning(f"No se pudieron borrar archivos locales: {e}")
                return b'Ok'
            else:
                return b'S3 Upload Fail'
        else:
            return b'Conversion Fail'

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