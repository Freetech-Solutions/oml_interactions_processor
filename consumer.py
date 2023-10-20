import pika
import ari
from ari import ARI
import json
import os
import logging

logging.basicConfig(level=logging.INFO)

ASTERISK_HOST = os.getenv('ARI_HOST', 'asterisk')
ASTERISK_PORT = os.getenv('ARI_PORT', '7088')
ASTERISK_USER = os.getenv('ARI_USER', 'default_user')
ASTERISK_PASS = os.getenv('ARI_PASS', 'default_pass')
RABBITMQ_HOST = os.getenv('RABBITMQ_HOSTNAME', 'localhost')


#def process_message(ch, method, properties, body):
def process_message(ari_client, ch, method, properties, body):
    
    logging.info(f"Received message: {body}")
    message_data = json.loads(body.decode("utf-8"))

    # Parametros del PSTN channel
    id_channel_pstn = message_data.get('id_channel')
    id_campaign = message_data.get('id_campaign')
    id_bridge = message_data.get('id_bridge')

    logging.info(f"******* CONSUMER : *** id_channel ***: {id_channel_pstn}, *** id_campaign ***: {id_campaign}, *** id_bridge ***: {id_bridge}")
    
    if id_channel_pstn and id_campaign:
        try:            
            # reproduzco MOH sobre el channel PSTN
            ari_client.start_moh(id_channel_pstn)
  
            app_args_str = f"{id_campaign},{id_channel_pstn},{id_bridge}"
            ari_client.originate_channel(endpoint='PJSIP/1004', app='Deliver', appArgs=app_args_str)

        except Exception as e:
            logging.error(f"Failed to originate call: {e}")
#        finally:
            # Assuming ari_client has a close() method, if not, remove this finally block
#            ari_client.close()
    else:
        logging.warning("Received message missing 'id_channel_pstn' or 'id_campaign'")

    ch.basic_ack(delivery_tag=method.delivery_tag)


def main():
    connection = None
    try:
        # Inicializa tu propio cliente ARI aquí, no el de la biblioteca `ari-py`.
        ari_client = ARI(user=ASTERISK_USER, password=ASTERISK_PASS, host=ASTERISK_HOST, port=ASTERISK_PORT)

        connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
        channel = connection.channel()

        channel.queue_declare(queue='Queue', durable=True)
        channel.basic_qos(prefetch_count=1)
        # Asegúrate de pasar el cliente ARI al callback
        on_message_callback = lambda ch, method, properties, body: process_message(ari_client, ch, method, properties, body)
        channel.basic_consume(queue='Queue', on_message_callback=on_message_callback)

        logging.info('Starting to consume messages from RabbitMQ')
        channel.start_consuming()
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        if connection:
            connection.close()

if __name__ == "__main__":
    main()
