import pika
import sys

def enviar_mensaje(cola, mensaje):
    conexion = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
    canal = conexion.channel()

    canal.queue_declare(queue=cola, durable=True)

    canal.basic_publish(exchange='',
                        routing_key=cola,
                        body=mensaje,
                        properties=pika.BasicProperties(
                            delivery_mode=2,  # Hacer que el mensaje sea persistente
                        ))
    print(f" [x] Enviado {mensaje}")
    conexion.close()


def callback(ch, method, properties, body):
    print(f" [x] Recibido {body.decode()}")


def recibir_mensaje(cola):
    conexion = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
    canal = conexion.channel()

    canal.queue_declare(queue=cola, durable=True)

    print(' [*] Esperando mensajes. Para salir presiona CTRL+C')
    canal.basic_consume(queue=cola, on_message_callback=callback, auto_ack=True)

    canal.start_consuming()


def contar_mensajes_en_cola(cola):
    conexion = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
    canal = conexion.channel()

    # Al declarar la cola con `passive=True`, no se creará una nueva cola y,
    # en cambio, se arrojará una excepción si no existe.
    resultado = canal.queue_declare(queue=cola, passive=True)
    num_mensajes = resultado.method.message_count

    print(f" [x] Hay {num_mensajes} mensaje(s) en la cola '{cola}'.")
    conexion.close()

def borrar_mensajes_de_cola(cola):
    try:
        conexion = pika.BlockingConnection(pika.ConnectionParameters(host='rabbitmq'))
        canal = conexion.channel()

        # El método queue_purge borra los mensajes de la cola
        method_frame = canal.queue_purge(queue=cola)

        # El objeto method_frame contiene la cantidad de mensajes borrados
        print(f"{method_frame.method.message_count} mensajes borrados de la cola {cola}")

        conexion.close()
    except Exception as e:
        print(f"Ocurrió un error al intentar borrar los mensajes de la cola: {e}")


if __name__ == '__main__':
    # El primer argumento es el nombre de la acción, el segundo es el nombre de la cola
    if len(sys.argv) < 3:
        sys.stderr.write("Uso: {0} <acción> <nombre_cola> [mensaje]\n".format(sys.argv[0]))
        sys.exit(1)

    accion = sys.argv[1]
    cola = sys.argv[2]  # Ahora la cola se toma del argumento en la línea de comandos

    if accion == 'enviar':
        if len(sys.argv) < 4:
            sys.stderr.write("Uso: {0} enviar <nombre_cola> <mensaje>\n".format(sys.argv[0]))
            sys.exit(1)
        mensaje = sys.argv[3]  # El mensaje también se toma del argumento en la línea de comandos
        enviar_mensaje(cola, mensaje)
    elif accion == 'contar':
        contar_mensajes_en_cola(cola)
    elif accion == 'borrar':
        borrar_mensajes_de_cola(cola)
    elif accion == 'recibir':
        recibir_mensaje(cola)
    else:
        sys.stderr.write("Acción no reconocida: {0}\n".format(accion))
        sys.stderr.write("Las acciones pueden ser 'enviar', 'contar', 'borrar', o 'recibir'\n")
        sys.exit(1)
