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
from botocore.exceptions import NoCredentialsError, ClientError

# Configura el logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuración de RabbitMQ
rabbitmq_host = os.getenv("RABBITMQ_HOST")
rabbitmq_queue = 'callrec_processor'

# Configuración de AWS S3
s3_bucket_name = os.getenv("S3_BUCKET_NAME")
aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
endpoint_url = os.getenv("S3_ENDPOINT", None)
region_name = os.getenv("S3_REGION_NAME", 'us-east-1')
storage_type = os.getenv('CALLREC_DEVICE', 's3-aws')

# Configuración del cliente S3 basada en el tipo de almacenamiento
try:
    s3 = boto3.client(
        's3',
        region_name=region_name if storage_type == 's3-aws' else None,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        endpoint_url=endpoint_url if storage_type != 's3-aws' else None,
        verify=(storage_type != 's3-no-check-cert')
    )
except Exception as e:
    logging.error(f"Failed to initialize S3 client: {e}")
    exit(1)

def convert_to_mp3(source_path, mp3_path):
    try:
        subprocess.run(['ffmpeg', '-i', source_path, '-codec:a', 'libmp3lame', '-qscale:a', '2', mp3_path], check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to convert to MP3: {e}")
        return False
    return True

def remove_silence(source_path):
    output_path = source_path.replace('.wav', '-trimmed.wav')
    command = [
        'sox', 
        source_path, 
        output_path, 
        'silence', '-l', 
        '1', '0.7', '3%',  
        '-1', '0.7', '3%' 
    ]
    try:
        subprocess.run(command, check=True)
        logging.info(f"Silencios eliminados de {source_path}, guardado en {output_path}")
        os.remove(source_path)
        return output_path
    except subprocess.CalledProcessError as e:
        logging.error(f"Error al quitar silencios: {e}")
        return source_path

def upload_to_s3(source_path, destination_path):
    try: 
        s3.upload_file(source_path, s3_bucket_name, destination_path, ExtraArgs={'Metadata': {'convert': 'yes', 'transcribe': 'yes'}})
    except (NoCredentialsError, ClientError) as e:
        logging.error(f"Error uploading to S3: {e}")
        return False
    return True

def process_audio_file(source_file, date_dialplan, process_split=False):
    source_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{source_file}"
    if not os.path.exists(source_path):
        logging.error(f"File not found: {source_path}")
        return
    
    base, ext = os.path.splitext(source_file)
    mp3_file = f"{base}.mp3"
    mp3_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{mp3_file}"

    logging.info(f'Processing original audio: {source_path}')
    if convert_to_mp3(source_path, mp3_path) and upload_to_s3(mp3_path, f"{date_dialplan}/{mp3_file}"):
        os.remove(mp3_path)
        os.remove(source_path)  # Removing original WAV file after successful conversion and upload

    if process_split:
        for suffix in ['-Rx', '-Tx']:
            channel_file = f"{base}{suffix}{ext}"
            channel_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{channel_file}"
            no_silence_path = remove_silence(channel_path)

            channel_mp3_file = f"{base}{suffix}.mp3"
            channel_mp3_path = f"/var/spool/asterisk/monitor/{date_dialplan}/{channel_mp3_file}"

            if convert_to_mp3(no_silence_path, channel_mp3_path) and upload_to_s3(channel_mp3_path, f"{date_dialplan}/{channel_mp3_file}"):
                os.remove(channel_mp3_path)
                os.remove(no_silence_path)

def callback(ch, method, properties, body):
    logging.info("Mensaje recibido desde RabbitMQ.")
    message = json.loads(body)
    file_name = message['fileName']
    date_file_name = message['dateFileName']
    split_channels = message.get('splitChannels', 'False') == 'True'

    if split_channels == True:
        process_audio_file(file_name, date_file_name, process_split=True)
    else:
        process_audio_file(file_name, date_file_name)

    ch.basic_ack(delivery_tag=method.delivery_tag)

def consume():
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()
    channel.queue_declare(queue=rabbitmq_queue, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=rabbitmq_queue, on_message_callback=callback)
    logging.info('Consumer ready. To exit, press CTRL+C.')
    channel.start_consuming()

if __name__ == "__main__":
    consume()
