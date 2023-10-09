import websocket
from ari import ARI
import rel
import os
import json
import sys 
import logging
import redis

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

redis_connection = redis.Redis(
    host=os.getenv('REDIS_HOSTNAME', 'localhost'),  # Puedes proporcionar un valor por defecto en caso de que la variable de entorno no esté configurada
    port=6379,
    db=2,
    decode_responses=True  # Para que las respuestas se decodifiquen como str y no como bytes
)

class CallManager:
    def __init__(self):
        self.bridge_id = None
        self.ari = ARI()  

    def handle_stasis_start(self, event):        
        # PSTN channel
        if 'caller' in event['channel']:  
            channel_id = event['channel']['id'] 
            self.ari.answer(channel_id)
            self.ari.playback(channel_id, 'beep')
            
            # Crear un bridge
            bridge_response = self.ari.create_bridge()
            self.bridge_id = bridge_response['id']  # Aquí estamos usando la propiedad de la clase
            
            # Agregar el canal entrante al bridge
            self.ari.add_channel_to_bridge(self.bridge_id, channel_id)

            # Originar un nuevo canal al agente PJSIP/1005 y añadirlo a tu aplicación Stasis
            self.ari.originate_channel('PJSIP/1004', 'context', 'exten', 'priority')

        # agent channel
        else:  
            channel_id = event['channel']['id'] 
            # Agregar el canal originado al bridge creado arriba
            self.ari.add_channel_to_bridge(bridge_id, channel_id)

    def handle_dial(self, event):
        dialstatus = event['dialstatus']
        logging.info(f"****** Dial Event - Status: {dialstatus} ********")

call_manager = CallManager()

def on_message(ws, message):
    #logging.info(f"Received Message: {message}")
    event_to_dict = json.loads(message)
    event = event_to_dict.get('type', 'default')

    if event == 'StasisStart':
        call_manager.handle_stasis_start(event_to_dict)
    elif event == 'Dial':
        call_manager.handle_dial(event_to_dict)
        logging.info("****** Ringing ********")
        

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
    ASTERISK_APP = os.getenv('ASTERISK_APP', 'survey')
    
    # Creamos la URI del WebSocket utilizando las variables
    ws_uri = f"ws://{ASTERISK_HOST}:{ASTERISK_PORT}/ari/events"
    ws_uri += f"?api_key={ASTERISK_USER}:{ASTERISK_PASS}&app=Queue"
    
    ws = websocket.WebSocketApp(
        ws_uri,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    ws.run_forever(dispatcher=rel, reconnect=5)
    rel.signal(2, rel.abort)  # Keyboard Interrupt
    rel.dispatch()
