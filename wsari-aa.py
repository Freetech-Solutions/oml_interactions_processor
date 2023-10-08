import websocket
from ari import ARI
import rel
import os
import json
import sys 
import logging

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

class SurveyAri:

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

    def get_sound(self):
        return self.survey_options[self.question]['audio']

    def get_options(self):
        return self.survey_options[self.question]['options']

    def get_type(self):
        return self.survey_options[self.question]['type']

    def next_question(self):
        if self.question < len(self.survey_options):
            self.question += 1

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
    logging.info(event_to_dict)
    #print(event_to_dict)

    if event == 'StasisStart':
        channel = event_to_dict['channel']['id']
        if channel not in memory_route.keys():
            memory_route[channel] = SurveyAri()

        ari_object.answer(event_to_dict['channel']['id'])
        ari_object.playback(
            channel,
            memory_route[channel].get_sound()
        )

    # if event == 'ChannelDtmfReceived':
    #     option = event_to_dict['digit']
    #     channel = event_to_dict['channel']['id']
    #     current_channel = memory_route[channel]
    #     if option in current_channel.get_options():
    #         ari_object.stop_playback(current_channel.playback_id)
    #         current_channel.answer_register(option)
    #         current_channel.attempts = 0

    # if event == 'PlaybackStarted':
    #     target = event_to_dict['playback']['target_uri']
    #     _, channel = target.split(':')
    #     memory_route[channel].playback_id = event_to_dict['playback']['id']

    # if event == 'PlaybackFinished':
    #     target = event_to_dict['playback']['target_uri']
    #     _, channel = target.split(':')
    #     current_channel = memory_route[channel]

        if not current_channel.is_finish():
            if current_channel.get_type() == 1:
                #current_channel.next_question()
                print("get type 1")

            if (current_channel.get_type() == 2 and
               not current_channel.is_answered()):
                print("get type 2")
                #current_channel.inc_attempts()

            ari_object.playback(
                channel,
                current_channel.get_sound()
            )
        else:
            ari_object.playback(
                channel,
                'tt-weasels'
            )
            ari_object.continue_call(channel)


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
    ws_uri += f"?api_key={ASTERISK_USER}:{ASTERISK_PASS}&app={ASTERISK_APP}"
    
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
