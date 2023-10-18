import pika
import os

class RabbitMQManager:
    def __init__(self):
        self.rabbitmq_host = os.getenv('RABBITMQ_HOSTNAME', 'rabbitmq')        
        self.connection = self.create_connection()
        self.channel = self.create_channel()

    def create_connection(self):
        return pika.BlockingConnection(pika.ConnectionParameters(self.rabbitmq_host))

    def create_channel(self):
        channel = self.connection.channel()
        return channel  # Ya no declaramos la cola aquí

    def publish_message(self, queue_name, message):
        try:
            # Declaramos la cola justo antes de enviar el mensaje.
            # Esto asegura que la cola exista antes de que se publique el mensaje,
            # y nos permite especificar el nombre de la cola en tiempo de ejecución.
            self.channel.queue_declare(queue=queue_name, durable=True) 

            self.channel.basic_publish(
                exchange='', 
                routing_key=queue_name,  # aquí usamos el argumento queue_name
                body=message
            )
            print(f" [x] Sent {message}")
        except Exception as e:
            print(f"An error occurred: {e}")

    def close_connection(self):
        self.connection.close()
