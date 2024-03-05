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

        interface = os.environ.get('FASTAGI_HOSTNAME', '0.0.0.0')

        self._fagi_server = pystrix.agi.FastAGIServer(interface=interface)

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

    def get_date(self, formato="%Y-%m-%d %H:%M:%S"):
        time_zone = os.getenv('TZ', 'UTC')
        tz = pytz.timezone(time_zone)
        now = datetime.datetime.now(tz)
        return now.strftime(formato)
    
    def get_redis_connection(self, db=0):    
        return redis.Redis(
            host=os.getenv('REDIS_HOSTNAME', 'redis'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=db,
            decode_responses=True
    )
    # ---- OML call logger postgres reportes_app_llamadalog ----
    # ---- OML call logger postgres reportes_app_llamadalog ----
    def omni_logger_conf(self, agi, *args, **kwargs):
        arguments = args[0]

        if len(arguments) < 14:
            self.write_time_stderr('Error: No se proporcionaron suficientes argumentos\n')
            return

        campana_id, callid, agente_id, event, numero_marcado, contacto_id, tipo_llamada, \
        tipo_campana, bridge_wait_time, duracion_llamada, archivo_grabacion, agente_extra_id, \
        campana_extra_id, numero_extra = arguments                
        
        date_formatted = self.get_date()

        llamadalog_dict = {
            'time': date_formatted,
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

        try:
            conn = psycopg2.connect(
                host=os.getenv('PGHOST'),
                port=os.getenv('PGPORT'),
                dbname=os.getenv('PGDATABASE'),
                user=os.getenv('PGUSER'),
                password=os.getenv('PGPASSWORD')
            )
            cursor = conn.cursor()

            insert_query = sql.SQL(
                'INSERT INTO reportes_app_llamadalog ({}) VALUES ({})'
            ).format(
                sql.SQL(',').join(map(sql.Identifier, llamadalog_dict.keys())),
                sql.SQL(',').join(map(sql.Placeholder, llamadalog_dict.keys()))
            )

            cursor.execute(insert_query, llamadalog_dict)
            conn.commit()
        except Exception as e:
            self.write_time_stderr(f'Error due to: {e}\n')
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        redis_key_camp = f'OML:CALLDATA:CAMP:{campana_id}'        
        field_campana = f'CALL_TYPE:{tipo_llamada}:{event}'
        self.event_camp_sum(redis_key_camp, field_campana)

        redis_key_agent = f'OML:CALLDATA:AGENT:{agente_id}'
        field_agent = f'CALL_TYPE:{tipo_llamada}:{event}'
        self.event_agent_sum(redis_key_agent, field_agent, event)
        
        redis_key_wait_time = f'OML:CALLDATA:WAIT-TIME:CAMP:{campana_id}'
        self.event_camp_queue_wait_time(redis_key_wait_time, bridge_wait_time, event)

    # Redis events CAMP INCRDB 
    def event_camp_sum(self, redis_key, field):
        try:
            redis_connection = self.get_redis_connection(db=2)
            redis_connection.hincrby(redis_key, field, 1)
        except redis.exceptions.RedisError as e:
            self.write_time_stderr(f"Redis camp_sum error: {e}")

    # Redis events AGENT INCRDB 
    def event_agent_sum(self, redis_key, field, event):
        try:
            redis_connection = self.get_redis_connection(db=2)
            if event in ["ANSWER", "CONNECT", "RINGNOANSWER", "DIAL", "CANCEL", "CONGESTION"]:
                redis_connection.hincrby(redis_key, field, 1)
        except redis.exceptions.RedisError as e:
            self.write_time_stderr(f"Redis agent_sum error: {e}")

    def event_camp_queue_wait_time(self, redis_key, wait_time, event):
        try:
            redis_connection = self.get_redis_connection(db=2)
            if event in ["CONNECT", "ABANDON"]:
                redis_connection.rpush(redis_key, wait_time)
        except redis.exceptions.RedisError as e:
            self.write_time_stderr(f"Redis event_queue error: {e}")

    # Redis Queue wait-time
    def event_camp_queue_wait_time(self, redis_key, wait_time, event):
        try:
            redis_connection = redis.Redis(
                host=os.getenv('REDIS_HOSTNAME'),
                port=6379,
                db=2,
                decode_responses=True
            )
 
            if event in ["CONNECT", "ABANDON"]:                        
                redis_connection.rpush(redis_key, wait_time)
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
        
        if len(arguments) < 2:
            self.write_time_stderr("Error: Insufficient arguments provided")
            return

        family_type, item_id = arguments[:2]
        family_key = f'OML:{family_type}:{item_id}'

        redis_connection = self.get_redis_connection(db=0)

        try:
            family_data = redis_connection.hgetall(family_key)
            if not family_data:
                self.write_time_stderr(f"Unable to get Family DATA for {family_key}")
                return

            for key, value in family_data.items():
                variable_name = f'__OML{family_type}{key}'
                agi.execute(pystrix.agi.core.SetVariable(variable_name, value))
        except redis.exceptions.RedisError as e:
            self.write_time_stderr(f"Error executing Redis command HGETALL for {family_key}: {e}")
        except Exception as e:
            self.write_time_stderr(f"Unable to set variable in channel due to {e}")
            raise e


    # --- Blacklist check if number is on the blacklist REDIS ---
    # --- Blacklist check if number is on the blacklist REDIS ---
    def omni_blacklist(self, agi, *args, **kwargs):
        if len(args[0]) < 1:  # Verifica que al menos un argumento ha sido proporcionado
            self.write_time_stderr("Error: No phone number provided")
            return

        phone_number = args[0][0]
        black_list_key = 'OML:BLACKLIST'
        redis_connection = self.get_redis_connection()

        try:
            is_black_listed = int(redis_connection.sismember(black_list_key, phone_number))
        except redis.exceptions.RedisError as e:
            self.write_time_stderr(f"Error executing Redis command SISMEMBER: {e}")
            is_black_listed = self.BLACKLIST_ERROR_CODE

        try:
            agi.execute(pystrix.agi.core.SetVariable('BLACKLIST', str(is_black_listed)))
        except Exception as e:
            self.write_time_stderr(f"Unable to set variable BLACKLIST in channel due to {e}")
            raise e


    # --- Set agent status ONCALL ---
    # --- Set agent status ONCALL ---
    def omni_agent_status(self, agi, *args, **kwargs):
        arguments = args[0]
        if len(arguments) < 2:
            self.write_time_stderr("Error: Insufficient arguments provided")
            return

        command = arguments[0]
        agent_id = arguments[1]
        agent_key = 'OML:AGENT:' + agent_id

        redis_connection = self.get_redis_connection()

        if command not in ['GET', 'SET']:
            self.write_time_stderr(f"Unknown command {command}")
        elif command == 'GET':
            try:
                agent_data = redis_connection.hgetall(agent_key)
                if not agent_data:
                    self.write_time_stderr(f"Unable to get Agent DATA for {agent_key}")
                    return

                # Configurar variables en el canal Asterisk AGI
                for var_name, var_value in agent_data.items():
                    agi_variable = '__OMLAGENT' + var_name.upper()
                    agi.execute(pystrix.agi.core.SetVariable(agi_variable, var_value))
            except redis.exceptions.RedisError as e:
                self.write_time_stderr(f"Error executing Redis command HGETALL: {e}")
        elif command == 'SET':
            data = {
                'STATUS': arguments[2],
                'TIMESTAMP': arguments[3],
                'CAMPAIGN': arguments[4] if len(arguments) >= 5 else '',
                'CONTACT_NUMBER': arguments[5] if len(arguments) >= 6 else '',
            }
            try:
                redis_connection.hset(agent_key, mapping=data)
            except redis.exceptions.RedisError as e:
                self.write_time_stderr(f"Error executing Redis command SET: {e}")


    # -- Survey Addon insert DTMF on Redis ----
    # -- Survey Addon insert DTMF on Redis ----
    def omni_survey_answer(self, agi, *args, **kwargs):
        if not args or len(args[0]) != 9:
            self.write_time_stderr("Error: Argumentos inesperados en omni_survey_answer. Se esperaban 9 argumentos.")
            return

        # Preparar los datos para ser almacenados en Redis
        data = json.dumps(args[0][0:9])
        family_key = 'OML:QUEUE:SURVEY_ANSWERS'

        redis_connection = self.get_redis_connection()

        try:
            redis_connection.rpush(family_key, data)
            root_logger.info(f"Datos de encuesta almacenados correctamente: {data}")
        except redis.exceptions.RedisError as e:
            self.write_time_stderr(f"Error al ejecutar el comando RPUSH en Redis: {e}")


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
