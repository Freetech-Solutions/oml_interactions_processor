import os
import re
import sys
import time
import django
import logging
import pystrix
import threading
import redis
from socket import setdefaulttimeout

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
        
        self._fagi_server.register_script_handler(
            re.compile('omni-blacklist'), self.omni_blacklist)
        
        self._fagi_server.register_script_handler(
            re.compile('omni-agent-status'), self.omni_agent_status)

        self._fagi_server.register_script_handler(
            re.compile('omni-survey-answer'), self.omni_survey_answer)

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

    def write_time_stderr(str_value):
        fecha = datetime.strftime(datetime.now(), '%Y-%m-%d %H:%M:%S.%f')
        sys.stderr.write("{0}: {1}".format(fecha, str_value))

    # Retrieve config from Redis and Set chanvars in order to pass to the dialplan
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



    # Blacklist check if number is on the blacklist REDIS
    def omni_blacklist(self, agi, *args, **kwargs):
        
        arguments = args[0]
        phone_number = arguments[0]

        black_list_key = 'OML:BLACKLIST'
        redis_connection = redis.Redis(
            host=os.getenv('REDIS_HOSTNAME'),
            port=6379,
            decode_responses=True
        )

        try:
            is_black_listed = int(redis_connection.sismember(black_list_key, phone_number))
        except redis.exceptions.RedisError as e:
            write_time_stderr("Error executing Redis command SISMEMBER: {0}".format(e))
            # Si falla el servicio, devuelve código de error
            is_black_listed = BLACKLIST_ERROR_CODE

        try:
            agi.set_variable('BLACKLIST', str(is_black_listed))
        except Exception as e:
            write_time_stderr("Unable to set variable BLACKLIST in channel due to {0}".format(e))
            raise e

    # Set or Get the Agent STATUS
    def omni_agent_status(self, agi, *args, **kwargs):
        
        arguments = args[0]
        
        command = arguments[0]
        agent_id = arguments[1]
        agent_key = 'OML:AGENT:' + agent_id

        redis_connection = redis.Redis(
            host=os.getenv('REDIS_HOSTNAME'),
            port=6379,
            decode_responses=True
        )

        if command not in ['GET', 'SET']:
            write_time_stderr("Unknown command {0}".format(command))
        elif command == 'GET':
            try:
                agent_data = redis_connection.hgetall(agent_key)
            except redis.exceptions.RedisError as e:
                write_time_stderr("Error executing Redis command HGETALL: {0}".format(e))
            else:
                if agent_data:
                    try:
                        agi.set_variable('__OMLAGENTNAME', agent_data['NAME'])
                        agi.set_variable('OMLAGENTSIP', agent_data['SIP'])
                        agi.set_variable('OMLAGENTSTATUS', agent_data['STATUS'])
                        agi.set_variable('PAUSE_ID', agent_data.get('PAUSE_ID', ''))
                    except Exception as e:
                        write_time_stderr("Unable to set variable in channel due to {0}".format(e))
                        raise e
                else:
                    write_time_stderr("Unable to get Agent DATA for {0}".format(agent_key))
        elif command == 'SET':
            data = {
                'STATUS': arguments[2],
                'TIMESTAMP': arguments[3],
                'CAMPAIGN': arguments[4] if len(arguments) >= 5 else '',
                'CONTACT_NUMBER': arguments[5] if len(arguments) >= 6 else '',
            }
            try:
                agent_data = redis_connection.hset(agent_key, mapping=data)
            except redis.exceptions.RedisError as e:
                write_time_stderr("Error executing Redis command SET: {0}".format(e))
                # Aquí puedes decidir cómo manejar el error al ejecutar el comando SET en Redis


    # Survey Addon
    def omni_survey_answer(self, sys_argv):
        agi = AGI()
        redis_connection = redis.Redis(
            host=os.getenv('REDIS_HOSTNAME'),
            port=6379,
            decode_responses=True
        )

        data = json.dumps(sys_argv[1:10])
        family_key = 'OML:QUEUE:SURVEY_ANSWERS'

        try:
            family_data = redis_connection.rpush(family_key, data)
        except redis.exceptions.RedisError as e:
            write_time_stderr("Error executing redis command RPUSH: {0}".format(e))


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
