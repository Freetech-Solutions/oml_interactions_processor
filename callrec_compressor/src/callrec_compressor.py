# worker/worker.py
import os
import json
import logging
import subprocess
import gearman
import boto3
from botocore.exceptions import NoCredentialsError, ClientError

# ───── Configuración general ─────
ASTERISK_MONITOR_PATH = '/var/spool/asterisk/monitor/'
GEARMAN_SERVER = os.getenv('GEARMAN_HOST', 'gearman:4730')
TASK_NAME = b'tel-callrec-compressor'

# ───── Logging estructurado ─────
logger = logging.getLogger("callrec_worker")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - [%(filename)s:%(lineno)d] - %(message)s'
)

# ───── Configuración de S3 ─────
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
    logger.info("Cliente S3 inicializado correctamente.")
except Exception as e:
    logger.exception("Fallo al inicializar cliente S3.")
    exit(1)

logger.info("--- Worker Iniciado ---")
logger.info(f"Intentando conectar a Gearman Job Server en {GEARMAN_SERVER}...")

# Creamos una instancia del Worker
gm_worker = gearman.GearmanWorker([GEARMAN_SERVER])


def upload_to_s3(source_path, destination_path):
    try:
        s3.upload_file(
            source_path, s3_bucket_name, destination_path,
            ExtraArgs={'Metadata': {'convert': 'yes', 'transcribe': 'yes'}}
        )
        logger.info(f"Subido a S3: {destination_path}")
        return True
    except (NoCredentialsError, ClientError) as e:
        logger.exception(f"Fallo en subida a S3 de {source_path}")
        return False


# ───── Funciones auxiliares ─────
def convert_to_mp3(source_path, mp3_path):
    try:
        subprocess.run(['ffmpeg', '-y', '-i', source_path, '-codec:a', 'libmp3lame', '-qscale:a', '2', mp3_path], check=True)
        logger.info(f"Convertido a MP3: {mp3_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.exception(f"Fallo en conversión MP3 de {source_path}")
        return False


# Función que realmente hace el trabajo
def task_process_audiofile(gearman_worker, gearman_job):
    try:
        logger.info(f"Recibido job '{TASK_NAME}' con datos: {gearman_job.data.decode('utf-8')}")

        # Corregido: usar gearman_job.data en lugar de task.arg
        data = json.loads(gearman_job.data.decode('utf-8'))
        source_file = data['fileName'] + '.wav'
        date_dialplan = data['dateFileName']

        source_path = f"{ASTERISK_MONITOR_PATH}{date_dialplan}/{source_file}"
        if not os.path.exists(source_path):
            logger.error(f"Archivo no encontrado: {source_path}")
            return b"File not found"

        base, ext = os.path.splitext(source_file)
        mp3_file = f"{base}.mp3"
        mp3_path = f"{ASTERISK_MONITOR_PATH}{date_dialplan}/{mp3_file}"

        logger.info(f"Procesando archivo: {source_path}")
        if convert_to_mp3(source_path, mp3_path) and upload_to_s3(mp3_path, f"{date_dialplan}/{mp3_file}"):
            os.remove(mp3_path)
            os.remove(source_path)
            logger.info(f"Procesamiento completado y archivos temporales eliminados")

        return b'Ok'

    except Exception as e:
        logger.exception(f"Error procesando el job")
        # En caso de error, devolvemos un mensaje de error
        return b'Fail'


try:
    gm_worker.register_task(TASK_NAME, task_process_audiofile)
    logger.info(f"Tarea '{TASK_NAME.decode('utf-8')}' registrada exitosamente.")
    logger.info(f"Esperando trabajos... (Presiona Ctrl+C para detener)")
    # Empezamos a esperar trabajos indefinidamente
    gm_worker.work()
except gearman.errors.ServerUnavailable:
    logger.error(f"Error: No se pudo conectar al Gearman Job Server en {GEARMAN_SERVER}.")
    logger.error("Asegúrate de que el servicio 'gearman' esté corriendo.")
except Exception as e:
    logger.exception(f"Ocurrió un error inesperado")


