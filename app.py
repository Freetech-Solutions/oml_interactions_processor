#!/usr/bin/env python3.12

# -*- coding: utf-8 -*-

# Copyright (C) 2024 Freetech Solutions

# This file is part of OMniLeads

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see http://www.gnu.org/licenses/.

import json
import os
import pika
import boto3
import subprocess
import logging
from botocore.exceptions import NoCredentialsError

# Configura el logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuración de RabbitMQ
rabbitmq_host = os.getenv("RABBITMQ_HOST")
rabbitmq_queue = 'callrec_processor'

# Configuración de AWS S3
s3_bucket_name = os.getenv("S3_BUCKET_NAME")
s3_endpoint = os.getenv("S3_ENDPOINT")
storage_type = os.getenv('CALLREC_DEVICE')
aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID") or None
aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or None
endpoint_url = os.environ.get("S3_ENDPOINT") or None
region_name = os.environ.get("S3_REGION_NAME") or 'us-east-1'

if storage_type == 's3-aws':
    s3 = boto3.client('s3', region_name)
elif storage_type == 's3-no-check-cert':
    s3 = boto3.client(
        's3',
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        endpoint_url=endpoint_url,
        verify=False)
else:
    s3 = boto3.client(
        's3',
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        endpoint_url=endpoint_url)

def convert_to_mp3(source_path, mp3_path):
    command = ['ffmpeg', '-i', source_path, '-codec:a', 'libmp3lame', '-qscale:a', '2', mp3_path]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError:
        logging.info("Failed to convert to MP3")
        exit(1)

def remove_silence(source_path):
    """Utiliza SoX para quitar los silencios del archivo de audio."""
    output_path = source_path.replace('.wav', '-nosilence.wav')
    command = ['sox', source_path, output_path, 'silence', '1', '0.1', '1%', 'reverse', 'silence', '1', '0.1', '1%', 'reverse']
    try:
        subprocess.run(command, check=True)
        os.remove(source_path)  # Eliminar el archivo original
        return output_path
    except subprocess.CalledProcessError as e:
        logging.error("Error al quitar los silencios: %s", e)
        return source_path  # En caso de error, retorna el path original
    
def upload_to_s3(source_path, destination_path):
    metadata = {'convert': 'yes', 'transcribe': 'yes'}
    try:
        s3.upload_file(source_path, s3_bucket_name, destination_path, ExtraArgs={'Metadata': metadata})
    except NoCredentialsError:
        logging.info("No se encontraron las credenciales de AWS.")
        exit(1)

def move_file_to_s3(source_file, date_dialplan, split_channels):
    source_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{source_file}"
    base, ext = os.path.splitext(source_file)
    mp3_file = f"{base}.mp3"
    mp3_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{mp3_file}"
    
    logging.info(f'archivo original: {source_path}')
    logging.info(f'archivo mp3: {mp3_path}')

    convert_to_mp3(source_path, mp3_path)
    
    destination_path = f"{date_dialplan}/{mp3_file}"
    upload_to_s3(mp3_path, destination_path)
    
    logging.info(f'Delete local files')
    os.remove(source_path)
    os.remove(mp3_path)

    logging.info(f'Split channels ? {split_channels}')

    if split_channels:
        logging.info("Processing split channels for Rx and Tx.")
        for suffix in ['-Rx', '-Tx']:
            channel_file = f"{base}{suffix}{ext}"
            channel_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{channel_file}"
            no_silence_path = remove_silence(channel_path)
            channel_mp3_file = f"{base}{suffix}.mp3"
            channel_mp3_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{channel_mp3_file}"

            convert_to_mp3(no_silence_path, channel_mp3_path)
            channel_destination_path = f"{date_dialplan}/{channel_mp3_file}"
            upload_to_s3(channel_mp3_path, channel_destination_path)
            os.remove(no_silence_path)
            os.remove(channel_mp3_path)

def callback(ch, method, properties, body):        
    logging.info("Mensaje recibido desde RabbitMQ.")

    message = json.loads(body)
    file_name = message['fileName']
    date_file_name = message['dateFileName']
    split_channels = message.get('splitChannels', 'False') == 'True' 

    # Ajustar para usar la nueva función que maneja la subida y la lógica de bifurcación
    move_file_to_s3(file_name, date_file_name, split_channels)

    # Enviar acuse de recibo
    ch.basic_ack(delivery_tag=method.delivery_tag)

def consume():
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()
    channel.queue_declare(queue=rabbitmq_queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=rabbitmq_queue, on_message_callback=callback)
    logging.info('Consumer ready. For quick press CTRL+C.')
    channel.start_consuming()

if __name__ == "__main__":
    consume()