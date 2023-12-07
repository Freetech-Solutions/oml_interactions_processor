import os
import re
import sys
import time
import datetime
import pytz
import logging
import pystrix
import threading
import redis
from socket import setdefaulttimeout
import psycopg2
from psycopg2 import sql
import json

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
stdout_handler.setFormatter(formatter)

root_logger.addHandler(stdout_handler)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

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
            re.compile('omni-logger-conf'), self.omni_logger_conf)
    
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


# -------------------------------------------------------------------------------------
#   AGIs OML
# -------------------------------------------------------------------------------------

    
    def write_time_stderr(self, message):
        root_logger.error(message)

    # ---- OML call logger postgres reportes_app_llamadalog ----
    # ---- OML call logger postgres reportes_app_llamadalog ----
    def omni_logger_conf(self, agi, *args, **kwargs):
        POSTGRES_HOST = os.getenv('PGHOST')
        POSTGRES_PORT = os.getenv('PGPORT')
        POSTGRES_DB = os.getenv('PGDATABASE')
        POSTGRES_USER = os.getenv('PGUSER')
        POSTGRES_PASS = os.getenv('PGPASSWORD')

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASS
        )
        cursor = conn.cursor()

        arguments = args[0]
        
        tz_variable = os.environ.get('TZ')

        if len(arguments) < 14:
            self.write_time_stderr('Error: No se proporcionaron suficientes argumentos\n')
            return

        if tz_variable:
            local_tz = pytz.timezone(tz_variable)
        else:
            local_tz = pytz.timezone('UTC')

        campana_id, callid, agente_id, event, numero_marcado, contacto_id, tipo_llamada, \
        tipo_campana, bridge_wait_time, duracion_llamada, archivo_grabacion, agente_extra_id, \
        campana_extra_id, numero_extra = arguments

        now = datetime.datetime.now(local_tz)
        #now = datetime.datetime.utcnow()
        now_formatted = now.strftime("%Y-%m-%d %H:%M:%S")

        llamadalog_dict = {
            'time': now_formatted,
            'callid': callid,
            'campana_id': campana_id,
            'tipo_campana': tipo_campana,
            'tipo_llamada': tipo_llamada,
            'agente_id': agente_id,
            'event': event,
            'numero_marcado': numero_marcado,
            'contacto_id': contacto_id,
            'bridge_wait_time': bridge_wait_time,
            'duracion_llamada': duracion_llamada,
            'archivo_grabacion': archivo_grabacion, 
            'agente_extra_id': agente_extra_id,
            'campana_extra_id': campana_extra_id,
            'numero_extra': numero_extra
        }

        # Insert to Postgres for history KPIs
        insert_query = sql.SQL(
            'INSERT INTO reportes_app_llamadalog ({}) VALUES ({})'
        ).format(
            sql.SQL(',').join(map(sql.Identifier, llamadalog_dict.keys())),
            sql.SQL(',').join(map(sql.Placeholder, llamadalog_dict.keys()))
        )

        try:
            cursor.execute(insert_query, llamadalog_dict)
            conn.commit()
        except Exception as e:
            self.write_time_stderr(f'Error due to: {e}\n')
        finally:
            cursor.close()
            conn.close()

        redis_key_camp = f'OML:REALTIME:CAMP:{campana_id}'        
        field_campana = f'CALL_TYPE:{tipo_llamada}:{event}'
        self.event_camp_sum(redis_key_camp, field_campana)

        redis_key_agent = f'OML:REALTIME:AGENT:{agente_id}'
        field_agent = f'CALL_TYPE:{tipo_llamada}:{event}'
        self.event_agent_sum(redis_key_agent, field_agent, event)
        
    # Redis INCRDB 
    def event_camp_sum(self, redis_key, field):
        try:
            redis_connection = redis.Redis(
                host=os.getenv('REDIS_HOSTNAME'),
                port=6379,
                db=2,
                decode_responses=True
            )
            redis_connection.hincrby(redis_key, field, 1)
        except redis.exceptions.RedisError as e:
            print(f"Error al incrementar el valor en Redis: {e}")
        except Exception as ex:
            print(f"Error inesperado: {ex}")

    def event_agent_sum(self, redis_key, field, event):
        try:
            redis_connection = redis.Redis(
                host=os.getenv('REDIS_HOSTNAME'),
                port=6379,
                db=2,
                decode_responses=True
            )
 
            if event in ["ANSWER", "CONNECT", "RINGNOANSWER", "DIAL"]:
                redis_connection.hincrby(redis_key, field, 1)
            else:
                print("nothing")
        except redis.exceptions.RedisError as e:
            print(f"Error al incrementar el valor en Redis: {e}")
        except Exception as ex:
            print(f"Error inesperado: {ex}")


    # --- Retrieve config from Redis and Set chanvars in order to pass to the dialplan ---
    # --- Retrieve config from Redis and Set chanvars in order to pass to the dialplan ---
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



    # --- Blacklist check if number is on the blacklist REDIS ---
    # --- Blacklist check if number is on the blacklist REDIS ---
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
            agi.execute(pystrix.agi.core.SetVariable('BLACKLIST', str(is_black_listed)))
        except Exception as e:
            write_time_stderr("Unable to set variable BLACKLIST in channel due to {0}".format(e))
            raise e


    # --- Set agent status ONCALL ---
    # --- Set agent status ONCALL ---
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
                        agi.execute(pystrix.agi.core.SetVariable('__OMLAGENTNAME', agent_data['NAME']))
                        agi.execute(pystrix.agi.core.SetVariable('OMLAGENTSIP', agent_data['SIP']))
                        agi.execute(pystrix.agi.core.SetVariable('OMLAGENTSTATUS', agent_data['STATUS']))
                        agi.execute(pystrix.agi.core.SetVariable('PAUSE_ID', agent_data.get('PAUSE_ID', '')))
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
                # Here you can decide how to handle the error when executing the SET command in Redis


    # -- Survey Addon insert DTMF on Redis ----
    # -- Survey Addon insert DTMF on Redis ----
    def omni_survey_answer(self, agi, *args, **kwargs):
        redis_connection = redis.Redis(
            host=os.getenv('REDIS_HOSTNAME', 'localhost'),
            port=6379,
            decode_responses=True
        )

        if args and len(args[0]) == 9:
            data = json.dumps(args[0][0:9])
        else:
            self.write_time_stderr("Error: Argumentos inesperados en omni_survey_answer")
            return

        #data = json.dumps(sys.argv[1:10])
        family_key = 'OML:QUEUE:SURVEY_ANSWERS'

        root_logger.info(data)

        try:
            family_data = redis_connection.rpush(family_key, data)
            # Considera manejar 'family_data' si es necesario
        except redis.exceptions.RedisError as e:
            self.write_time_stderr(f"Error executing redis command RPUSH: {e}")


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
