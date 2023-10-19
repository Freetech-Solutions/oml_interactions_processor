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
            if args:
                id_camp = args[0]  # Asumiendo que "Queue" es el primer argumento
                custom_arg = args[1] if len(args) > 1 else None  # "nombre_argumento" es el segundo argumento

            logging.info(f"Argumentos recibidos: IDCAMP: {id_camp}")

            if event.get('channel', {}).get('dialplan', {}).get('context') == 'sub-oml-campaign-3':
                # Creamos el bridge cuando recibimos la primera llamada desde la PSTN           
                bridge = self.ari.create_bridge()
                if bridge is not None and 'id' in bridge:
                    self.bridge_id = bridge.get('id') 
                    self.handle_pstn_channel(event, id_camp)
                else:
                    logging.error("Failed to create bridge or 'id' not present in the response.") 
            else:
                self.handle_agent_channel(event)
        except Exception as e:
            logging.error(f"Error handling stasis start: {str(e)}")    

    def handle_pstn_channel(self, event, id_camp):
        logging.info(f"********* PSTN INBOUND Received Message: {event}")
        channel_id = event['channel']['id'] 
        
        try:
            self.ari.answer(channel_id)        
            
            if not isinstance(channel_id, str):
                logging.error(f"Unexpected type for channel_id: {type(channel_id)}")
                return

            if self.bridge_id is None:
                logging.error("Bridge is not created")
                return

            if not hasattr(self, "client"):
                logging.error("self.client is not configured")
                return

            # Agregar el canal PSTN al bridge
            result = self.ari.add_channel_to_bridge(self.bridge_id, channel_id)
              
            # Publicar mensaje a RabbitMQ usando modulo rabbitmq_manager.py
            #self.rabbitmq_manager.publish_message('Queue', f'Channel ID: {channel_id} - Camp ID: {id_camp}')

            message_dict = {
                'channel_id': channel_id,
                'id_campaign': id_camp
            }
            message_json = json.dumps(message_dict)

            self.rabbitmq_manager.publish_message('Queue', message_json)

            
        except Exception as e:
            logging.error(f"Error handling PSTN channel: {str(e)}")


    # def handle_agent_channel(self, event):
    #     logging.info(f"********* AGENT Channel Received Message: {event}")
    #     channel_id = event['channel']['id'  ] 
        
    #     try: 
    #         self.ari.playback(channel_id, 'beep')
    #         # Agregar el canal originado al bridge creado arriba        
    #         result = self.ari.add_channel_to_bridge(self.bridge_id, channel_id)
    #         # if result is None or 'error' in result:
    #         #     logging.error("Failed to add AGENT channel to bridge.")
    #         #     return
        
    #     except Exception as e:
    #         logging.error(f"Error handling AGENT channel: {str(e)}")


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
    ASTERISK_APP = os.getenv('ASTERISK_APP', 'Queue')

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