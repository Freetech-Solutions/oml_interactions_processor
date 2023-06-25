import os
import re
import sys
import time
import django
import logging
import pystrix
import threading
import redis

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Fastagi.settings')
django.setup()


class FastAGIServer(threading.Thread):

    _fagi_server = None  # The FastAGI server controlled by this thread

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

        self._fagi_server = pystrix.agi.FastAGIServer(interface='0.0.0.0')

        self._fagi_server.register_script_handler(
            re.compile('variables'), self.variables)

        self._fagi_server.register_script_handler(
            re.compile('channel-variables'), self.channel_variables)

        self._fagi_server.register_script_handler(
            re.compile('dial-app'), self.dial_app)

        self._fagi_server.register_script_handler(
            re.compile('say-alfa-digit'), self.say_alfa_digit)

        self._fagi_server.register_script_handler(
            re.compile('omni-retrieve-conf'), self.omni_retrieve_conf)
        
# -------------------------------------------------------------------------------------
#   AGI Examples
# -------------------------------------------------------------------------------------
    def variables(self, agi, *args, **kwargs):
        
        argumento = args[0]
        
        agi.execute(pystrix.agi.core.SetVariable('DESCRIPCION',
                                                 'Mostramos variables')
                    )
        agi.execute(pystrix.agi.core.SetVariable('DESTINO', '102'))
        agi.execute(pystrix.agi.core.SetVariable('TIEMPO', '10'))
        agi.execute(pystrix.agi.core.SetVariable('OPCIONES', 'TtR'))
        agi.execute(pystrix.agi.core.SetVariable('ARGUMENTO1', argumento[0]))
        agi.execute(pystrix.agi.core.SetVariable('ARGUMENTO2', argumento[1]))
        agi.execute(pystrix.agi.core.SetVariable('ARGUMENTO3', argumento[2]))


    def channel_variables(self, agi, *args, **kwargs):
        agi.execute(pystrix.agi.core.SetVariable('CHANVAR2',
                    agi.execute(pystrix.agi.core.GetFullVariable('${EXTEN}'))
            )
        )
        agi.execute(pystrix.agi.core.SetVariable('CHANVAR1',
                    agi.execute(pystrix.agi.core.GetFullVariable('${CONTEXT}'))
            )
        )
        agi.execute(pystrix.agi.core.SetVariable('CHANVAR3',
                    agi.execute(
                        pystrix.agi.core.GetFullVariable('${PRIORITY}'))
            )
        )
        agi.execute(pystrix.agi.core.SetVariable('CHANVAR4',
                    agi.execute(pystrix.agi.core.GetFullVariable('${CHANNEL}'))
            )
        )
        agi.execute(pystrix.agi.core.SetVariable('CHANVAR5',
                    agi.execute(
                        pystrix.agi.core.GetFullVariable('${CALLERID(all)}'))
            )
        )

    def dial_app(self, agi, *args, **kwargs):
        agi.execute(pystrix.agi.core.Exec('Dial',
                    options=('IAX2/102', '50', 'TtR'))
                    )

    def say_alfa_digit(self, agi, *args, **kwargs):
        agi.execute(pystrix.agi.core.SayDigits(1234)
                    )

        agi.execute(pystrix.agi.core.SayAlpha('abcd')
                    )

# -------------------------------------------------------------------------------------
#   Agi OML
# -------------------------------------------------------------------------------------
    
    def omni_retrieve_conf(self, agi, *args, **kwargs):

        arguments = args[0]
        
        family_type = arguments[0]
        item_id = arguments[1]

        redis_connection = redis.Redis(
            host=os.getenv('REDIS_HOSTNAME'),
            port=6379,
            decode_responses=True
        )

        family_key = f'OML:{family_type}:{item_id}'

        try:
            family_data = redis_connection.hgetall(family_key)
        except redis.exceptions.RedisError as e:
            write_time_stderr(f"Error executing Redis command HGETALL: {e}")
        else:
            if family_data:
                for key, value in family_data.items():
                    variable_name = f'__OML{family_type}{key}'
                    try:
                        agi.execute(pystrix.agi.core.SetVariable(variable_name, value))
                    except Exception as e:
                        write_time_stderr(f"Unable to set variable in channel due to {e}")
                        raise e
            else:
                write_time_stderr(f"Unable to get Family DATA for {family_key}")



    def kill(self):
        self._fagi_server.shutdown()

    def run(self):
        self._fagi_server.serve_forever()


if __name__ == '__main__':
    fastagi_core = FastAGIServer()
    fastagi_core.start()

    while fastagi_core.is_alive():
        time.sleep(1)
    fastagi_core.kill()
