import requests
from requests.auth import HTTPBasicAuth
import json

# Configuración de RabbitMQ
rabbitmq_user = 'guest'  # o tu usuario
rabbitmq_password = 'guest'  # o tu contraseña
rabbitmq_host = 'rabbitmq'  # o tu host
rabbitmq_port = '15672'  # puerto por defecto para la interfaz de administración de RabbitMQ
queue_name = 'my_queue'
vhost = '/'  # o tu vhost

# Construir la URL
url = f'http://{rabbitmq_host}:{rabbitmq_port}/api/queues/{vhost}/{queue_name}'

# Realizar la solicitud
response = requests.get(url, auth=HTTPBasicAuth(rabbitmq_user, rabbitmq_password))

# Verificar si la solicitud fue exitosa
if response.status_code == 200:
    queue_info = response.json()
    
    # Imprimir información importante sobre la cola
    print(json.dumps(queue_info, indent=4))
    
    # Puedes acceder a diferentes partes de la información de la cola, por ejemplo:
    print(f"Messages Ready: {queue_info.get('messages_ready')}")
    print(f"Messages Unacknowledged: {queue_info.get('messages_unacknowledged')}")
    print(f"Total messages: {queue_info.get('messages')}")
else:
    print(f'Failed to retrieve queue information. Status code: {response.status_code}')
    print(response.text)

