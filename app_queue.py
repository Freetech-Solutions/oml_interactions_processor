import websocket
import ari
from ari import ARI
import rel
import os
import json
import sys 
import logging
import redis

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
    #     self.client = self.setup_ari_client()

    def client(self):
        try:
            # Obtain environment variables within this method
            ASTERISK_USER = os.getenv('ARI_USER', 'default_user')
            ASTERISK_PASS = os.getenv('ARI_PASS', 'default_pass')
            ASTERISK_HOST = os.getenv('ARI_HOST', 'asterisk')
            ASTERISK_PORT = os.getenv('ARI_PORT', '7088')

            ari_client = ari.connect(f'http://{ASTERISK_HOST}:{ASTERISK_PORT}/', ASTERISK_USER, ASTERISK_PASS)
            return ari_client
        except Exception as e:
            print(f"Error setting up ARI client: {str(e)}")
            return None   

    def handle_stasis_start(self, event):        
        if 'caller' in event['channel']:  
            self.handle_pstn_channel(event)
        else:  
            self.handle_agent_channel(event)

    def handle_pstn_channel(self, event):        
        channel_id = event['channel']['id'] 
        self.ari.answer(channel_id)
        self.ari.playback(channel_id, 'beep')
            
        # if not isinstance(channel_id, str):
        #     logging.error(f"Unexpected type for channel_id: {type(channel_id)}")
        #     return

        # Asegúrate de que self.client está configurado correctamente
        if not hasattr(self, "client"):
            logging.error("self.client is not configured")
            return
        

        self.ari.originate_channel('PJSIP/1004', 'some_context', 'some_exten', 1)


        # Obtener el objeto de canal usando ari-py y el channel_id
        # try:
        #     channel = self.client.channels.get(channelId=channel_id)
        # except Exception as e:
        #     logging.error(f"Error retrieving channel object: {str(e)}")
        #     return

        # # Iniciar Music On Hold
        # try:
            
        #     self.ari.start_moh(channel_id)
        # except Exception as e:
        #     logging.error(f"Error starting MOH: {str(e)}")
        #     return
    
    def handle_agent_channel(self, event):
        logging.info(f"handle agent channel, pre-channe_id =")
        channel_id = event['channel']['id'  ] 
        # Agregar el canal originado al bridge creado arriba
        self.ari.add_channel_to_bridge(self.bridge_id, channel_id)

    def handle_dial(self, event):
        dialstatus = event['dialstatus']
        logging.info(f"****** Dial Event - Status: {dialstatus} ********")

call_manager = CallManager()

def on_message(ws, message):
    logging.info(f"Received Message: {message}")
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
    ASTERISK_APP = os.getenv('ASTERISK_APP', 'Queue')

    # Creamos la URI del WebSocket utilizando las variables
    ws_uri = f"ws://{ASTERISK_HOST}:{ASTERISK_PORT}/ari/events"
    ws_uri += f"?api_key={ASTERISK_USER}:{ASTERISK_PASS}&app={ASTERISK_APP}"
    
    ws = websocket.WebSocketApp(
        ws_uri,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    ws.run_forever(dispatcher=rel, reconnect=5)
    rel.signal(2, rel.abort)
    rel.dispatch()
