import requests
import os

class ARI:
    def __init__(self, user=None, password=None, host=None, port=None):
        self.host = host if host is not None else os.getenv('ARI_HOST', 'localhost')
        self.port = port if port is not None else os.getenv('ARI_PORT', '8088')
        self.user = user if user is not None else os.getenv('ARI_USER', 'default_user')
        self.password = password if password is not None else os.getenv('ARI_PASS', 'default_pass')

    def post(self, route, payload=None, headers=None):
        uri = f'http://{self.host}:{self.port}/ari/{route}'
        print(f"URI: {uri}, Payload: {payload}, Headers: {headers}")
        response = requests.post(uri, auth=(self.user, self.password), json=payload, headers=headers)
        try:
            return response.json()
        except ValueError:
            print(f"Error parsing JSON: {response.text}")
            return response

    def get(self, route):
        uri = f'http://{self.host}:{self.port}/ari/{route}'
        return requests.post(uri, auth=(self.user, self.password))

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
