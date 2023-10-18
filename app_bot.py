import websocket
from ari import ARI
import rel
import os
import json
import sys 
import logging

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

class Queue:

    survey_options = [
        {
            'audio': 'tt-weasels',
            'type': 1,
            'options': []
        },
        {
            'audio': 'demo-congrats',
            'type': 2,
            'options': ['1', '2', '3', '4', '5']
        },
        {
            'audio': 'custom/P2_ENC',
            'type': 2,
            'options': ['1', '2', '3', '4', '5']
        },
        {
            'audio': 'custom/P3_ENC',
            'type': 2,
            'options': ['1', '2', '3', '4', '5']
        },
    ]

    def __init__(self, max_attempts=3):
        self.question = 0
        self.sound = None
        self.options = None
        self.attempts = 0
        self.playback_id = None
        self.answers = {}
        self.max_attempts = max_attempts

    def inc_attempts(self):
        self.attempts += 1

    def answer_register(self, answer):
        if self.question not in self.answers.keys():
            self.answers[self.question] = answer
            self.next_question()

    def is_finish(self):
        return (True if self.question >= len(self.survey_options) or
                self.attempts >= self.max_attempts else False)

    def is_answered(self):
        return self.question in self.answers

memory_route = {}

def on_message(ws, message):
    ari_object = ARI()
    event_to_dict = json.loads(message)
    event = event_to_dict.get('type', 'default')

    if event == 'StasisStart':
        channel_id = event_to_dict['channel']['id']
        
        # Responder al canal de la llamada entrante
        ari_object.answer(channel_id)
        
        # Reproducir un mensaje de bienvenida o música en espera
        ari_object.playback(channel_id, 'tt-weasels')
        
        # Crear un bridge
        bridge_response = ari_object.create_bridge()
        bridge_id = bridge_response['id']  # Asegúrate de que tu método `post` devuelva la respuesta JSON parseada
        
        # Agregar el canal entrante al bridge
        ari_object.add_channel_to_bridge(bridge_id, channel_id)

        # Originar un nuevo canal al agente PJSIP/1005 y añadirlo a tu aplicación Stasis
        # (aquí necesitarías manejar la lógica cuando este canal entre en Stasis y entonces agregarlo al bridge)
        ari_object.originate_channel('PJSIP/1005', 'some_context', 'some_exten', 1)

    elif event == 'StasisStart':  # Este debería ser un segundo evento StasisStart para el agente
        channel_id = event_to_dict['channel']['id']
        
        # La lógica aquí debería buscar el bridge_id asociado a la llamada original y agregar el nuevo canal a él.
        # bridge_id = obtener_bridge_id_asociado_a_la_llamada_original_de_alguna_manera
        
        ari_object.add_channel_to_bridge(bridge_id, channel_id)

    elif event == 'PlaybackFinished':
        # La lógica aquí puede hacer cualquier limpieza o acción adicional después de que la reproducción ha terminado.
        pass

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
