import pika
import json
import logging
from ari import ARI
import os

class RabbitMQWorker:
    def __init__(self, queue_name):  # Añade queue_name como un parámetro
        # Configuración inicial de RabbitMQ
        self.rabbitmq_server = os.getenv('RABBITMQ_SERVER', 'rabbitmq')
        self.queue_name = queue_name  # Usa el parámetro aquí
              
        # Configuración inicial de ARI
        self.ari_client = ARI()

    def callback(self, ch, method, properties, body):
        # Esta función será llamada por cada mensaje recibido en la cola
        logging.info(f" [x] Received {body}")

        try:
            # Convertir el cuerpo del mensaje de bytes a dict
            message = json.loads(body)
            
            # Extraer el channel_id del mensaje
            channel_id_from_queue = message.get('channel_id')  # Asegúrate de que 'channel_id' sea la clave correcta en tu mensaje

            # Originar una nueva llamada hacia PJSIP/1004
            originate_response = self.ari_client.originate_channel('PJSIP/1004', 'your-stasis-app')  # Reemplaza 'your-stasis-app' con el nombre real de tu aplicación Stasis

            if originate_response and 'id' in originate_response:
                new_channel_id = originate_response['id']
                
                # Agregar el nuevo canal al puente con channel_id_from_queue
                self.ari_client.add_channel_to_bridge(channel_id_from_queue, new_channel_id)
            else:
                logging.error("Failed to originate channel or 'id' not present in the response.")
                
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON message: {str(e)}")
        except Exception as e:
            logging.error(f"Error processing message: {str(e)}")

        # Aceptar el mensaje como procesado.
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def start(self):
        # Establecer conexión con RabbitMQ
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.rabbitmq_server))
        channel = connection.channel()

        # Declarar la cola, por si aún no existe
        channel.queue_declare(queue=self.queue_name, durable=True)  # Durable es True para asegurarse de que los mensajes no se pierdan.

        # Suscribirse a la cola
        channel.basic_consume(queue=self.queue_name, on_message_callback=self.callback)

        logging.info(f" [*] Waiting for messages in {self.queue_name}. To exit press CTRL+C")
        channel.start_consuming()

if __name__ == '__main__':
    # Instancia el analizador
    parser = argparse.ArgumentParser(description="RabbitMQ Worker for processing queues")
    # Añade el argumento esperado
    parser.add_argument("queue", help="The name of the queue to process")
    # Analiza los argumentos proporcionados
    args = parser.parse_args()

    # Pasa el nombre de la cola al worker
    worker = RabbitMQWorker(queue_name=args.queue)
    worker.start()