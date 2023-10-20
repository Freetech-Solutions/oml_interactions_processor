import websocket
import ari
from ari import ARI
import rel
import os
import json
import sys 
import logging
import redis
import signal
import traceback 
from rabbitmq_manager import RabbitMQManager

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

redis_connection = redis.Redis(
    host=os.getenv('REDIS_HOSTNAME', 'localhost'),
    port=6379,
    db=2,
    decode_responses=True
)

class CallManager:
    def __init__(self):
        self.bridge_id = None
        self.ari = ARI()
        self.rabbitmq_manager = RabbitMQManager()

        # Obtain environment variables within this method
        ASTERISK_USER = os.getenv('ARI_USER', 'default_user')
        ASTERISK_PASS = os.getenv('ARI_PASS', 'default_pass')
        ASTERISK_HOST = os.getenv('ARI_HOST', 'asterisk')
        ASTERISK_PORT = os.getenv('ARI_PORT', '7088')
        ASTERISK_APP = os.getenv('ASTERISK_APP', 'Queue')
        

    def client(self):
        try:
            ari_client = ari.connect(f'http://{ASTERISK_HOST}:{ASTERISK_PORT}/', ASTERISK_USER, ASTERISK_PASS)
            return ari_client
        except Exception as e:
            logging.error(f"Error setting up ARI client: {str(e)}")
            logging.error(traceback.format_exc()) 
            return None       
        

    def handle_stasis_start(self, event):         
        try:        
            args = event.get('args', [])
            
            logging.info(f"Argumentos crudos recibidos: {args}")

            # Ahora, en lugar de dividir una cadena, directamente asignamos los valores desde la lista de argumentos
            if args and len(args) == 3:  # Si hay exactamente 3 argumentos, como esperamos
                id_campaign, id_channel_pstn, id_bridge = args
            else:
                logging.error("Número incorrecto de argumentos recibidos.")
                return  # Es importante salir aquí si los argumentos no son los que esperábamos

            logging.info(f"Argumentos recibidos: IDCAMP: {id_campaign}, ID_CHANNEL_PSTN: {id_channel_pstn}, ID_BRIDGE: {id_bridge}")

            channel_id = event['channel']['id']

            # Aquí, implementarías la lógica necesaria con los argumentos extraídos.
            try: 
                self.ari.playback(channel_id, 'beep')
                
                self.ari.stop_moh(id_channel_pstn)

                # Aquí podrías necesitar usar id_bridge en lugar de self.bridge_id
                result = self.ari.add_channel_to_bridge(id_bridge, channel_id)
                
            except Exception as e:
                logging.error(f"Error handling AGENT channel: {str(e)}")

        except Exception as e:
            logging.error(f"Error handling stasis start: {str(e)}")
            
    def handle_stasis_end(self, event):
        channel_id = event.get('channel', {}).get('id')
        
        if channel_id:
            # Aquí, podrías verificar si este canal está en tu puente.
            # Si es así, entonces procede a verificar si hay otros canales en el puente.
            active_channels = self.ari.get_channels_in_bridge(self.bridge_id)
            
            if active_channels:
                if len(active_channels) == 1:  # Solo queda un canal; podría ser el que se está desconectando.
                    self.ari.hangup_channel(active_channels[0])  # Desconectar el último canal.
                    self.ari.destroy_bridge(self.bridge_id)  # Destruir el puente.
                    self.bridge_id = None  # Restablecer el ID del puente si lo estás almacenando.
                else:
                    # Si hay más canales, implementa la lógica que consideres necesaria.
                    pass
            else:
                # No hay canales activos, es seguro destruir el puente.
                self.ari.destroy_bridge(self.bridge_id)
                self.bridge_id = None  # Restablecer el ID del puente si lo estás almacenando.
        else:
            logging.error("StasisEnd event without channel ID")


    def handle_dial(self, event):
        dialstatus = event['dialstatus']
        logging.info(f"****** DIAL ag channel Event - Status: {dialstatus} ********")

call_manager = CallManager()

def on_message(ws, message):
    #logging.info(f"Received Message: {message}")
    event_to_dict = json.loads(message)
    event = event_to_dict.get('type', 'default')

    if event == 'StasisStart':
        call_manager.handle_stasis_start(event_to_dict)
    elif event == 'Dial':
        call_manager.handle_dial(event_to_dict)
    elif event == 'StasisEnd':
        call_manager.handle_stasis_end(event_to_dict)

def on_error(ws, error):
    logging.info("***** ERROR *****")

def on_close(ws, close_status_code, close_msg):
    logging.info("Closed connection")

def on_open(ws):
    logging.info("Opened connection")


if __name__ == "__main__":
    
    # Obtenemos las variables de entorno
    ASTERISK_USER = os.getenv('ARI_USER', 'default_user')
    ASTERISK_PASS = os.getenv('ARI_PASS', 'default_pass')
    ASTERISK_HOST = os.getenv('ARI_HOST', 'asterisk')
    ASTERISK_PORT = os.getenv('ARI_PORT', '7088')
    ASTERISK_APP = os.getenv('ASTERISK_APP', 'Deliver')

    # Creamos la URI del WebSocket utilizando las variables
    ws_uri = f"ws://{ASTERISK_HOST}:{ASTERISK_PORT}/ari/events"
    ws_uri += f"?api_key={ASTERISK_USER}:{ASTERISK_PASS}&app={ASTERISK_APP}"

    # Configuración del WebSocket
    ws = websocket.WebSocketApp(
        ws_uri,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    # Función para manejar la señal de cierre
    def signal_handler(signum, frame):
        logging.info("Signal received, closing connection")
        ws.close()

    # Registro de la señal de interrupción
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Iniciar el loop de eventos del WebSocket
    ws.run_forever()