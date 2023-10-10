import requests
import os
import logging

class ARI:
    def __init__(self, user=None, password=None, host=None, port=None):
        self.host = host if host is not None else os.getenv('ARI_HOST', 'acd')
        self.port = port if port is not None else os.getenv('ARI_PORT', '7088')
        self.user = user if user is not None else os.getenv('ARI_USER', 'omnileads')
        self.password = password if password is not None else os.getenv('ARI_PASS', '5_MeO_DMT')

    def post(self, route, payload=None, headers=None):
        uri = f'http://{self.host}:{self.port}/ari/{route}'
        logging.info(f"URI: {uri}, Payload: {payload}, Headers: {headers}")
        response = requests.post(uri, auth=(self.user, self.password), json=payload, headers=headers)

        # Verificar si la respuesta contiene un cuerpo antes de intentar analizarlo como JSON
        if response.status_code == 204 or not response.text:
            logging.info(f"No content in response. Status Code: {response.status_code}")
            return None  # o cómo prefieras manejar este caso
        else:
            try:
                response_json = response.json()
                logging.info(f"Response: {response_json}")
                return response_json
            except ValueError:
                logging.error(f"Error parsing JSON: {response.text}, Status Code: {response.status_code}")
                return response

    def get(self, route):
        uri = f'http://{self.host}:{self.port}/ari/{route}'
        response = requests.get(uri, auth=(self.user, self.password))
        try:
            return response.json()
        except ValueError:
            logging.error(f"GET Error parsing JSON: {response.text}, Status Code: {response.status_code}")
            return response

    def delete(self, route):
        uri = f'http://{self.host}:{self.port}/ari/{route}'
        return requests.delete(uri, auth=(self.user, self.password))

    def playback(self, channel_id, sound):
        route = f'channels/{channel_id}/play?media=sound:{sound}'
        return self.post(route)

    def stop_playback(self, playback_id):
        route = f'playbacks/{playback_id}'
        return self.delete(route)

    def get_playback(self, playback_id):
        route = f'playbacks/{playback_id}'
        return self.get(route)

    def answer(self, channel_id):
        route = f'channels/{channel_id}/answer'
        return self.post(route)

    def continue_call(self, channel_id):
        route = f'channels/{channel_id}/continue'
        return self.post(route)

    def create_channel(self, channel):
        route = f'channels?endpoint={channel}&app=survey'
        return self.post(route)
    
    def add_channel_to_bridge(self, bridge_id, channel_id):
        route = f'bridges/{bridge_id}/addChannel'
        payload = {'channel': channel_id}
        return self.post(route, payload)

    def create_bridge(self, bridge_type='mixing'):
        route = 'bridges'
        payload = {'type': bridge_type}
        return self.post(route, payload)

    def add_channel_to_bridge(self, bridge_id, channel_id):
        route = f'bridges/{bridge_id}/addChannel'
        payload = {'channel': channel_id}
        return self.post(route, payload)

    def originate_channel(self, endpoint, context, exten, priority):
        route = 'channels'
        payload = {
            'endpoint': endpoint,
            'context': context,
            'exten': exten,
            'priority': priority,
            'app': 'my_stasis_app'  # reemplaza con el nombre de tu aplicación Stasis
        }
        return self.post(route, payload)


    def safe_hangup(self, channel_id):
        try:
            channel.hangup()
        except HTTPError as e:
            # Ignore 404's, since channels can go away before we get to them
            if e.response.status_code != requests.codes.not_found:
                raise

    def start_moh(self, channel):
        channel.startMoh(route, payload)     