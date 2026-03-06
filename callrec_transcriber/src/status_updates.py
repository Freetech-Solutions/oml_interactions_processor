"""
Class for notifying events via django-channels
"""
import os
import re
import django
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import logging
from django.db import connection, transaction
from django.conf import settings
import redis

# Formato S3: YYYY-MM-DD/callid.mp3 -> normalizar a callid para coincidir con la tabla de búsqueda
_CALLID_S3_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}/")


def normalize_callid_for_db(callid: str) -> str:
    """
    Convierte callid de clave S3 (ej. 2026-03-05/1772722418.1.mp3) al formato corto
    (1772722418.1) que usa la búsqueda de grabaciones y evita filas duplicadas en
    reportes_app_speechanalysis.
    """
    if not callid or not isinstance(callid, str):
        return callid
    if _CALLID_S3_PREFIX.match(callid):
        return callid.split("/", 1)[1].removesuffix(".mp3").removesuffix(".MP3")
    return callid


logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)s %(name)s - %(message)s'
)
logger = logging.getLogger('callrec_transcriber')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_settings")
django.setup()


def create_redis_connection(db=0):
    redis_connection = redis.Redis(
        host=settings.REDIS_HOSTNAME,
        port=settings.REDIS_PORT,
        db=db,
        decode_responses=True)
    return redis_connection


class DjangoChannelsNotifyer():
    _redis_connection = None

    @property
    def redis_connection(self):
        if self._redis_connection is None:
            self._redis_connection = create_redis_connection(db=2)
        return self._redis_connection

    def notify(self, recipient, payload):
        channel_layer = get_channel_layer()
        GROUP_USER_OBJ = 'supervisor-dialer-{user_id}'
        group_name = GROUP_USER_OBJ.format(user_id=recipient)
        async_to_sync(channel_layer.group_send)(
            group_name,
            {"type": "broadcast", "payload": payload},
        )
        logger.info('Notified: ' + group_name)

    def notify_speech_analytics(self, payload):
        """ En caso de que haya suscriptos al evento. Notifico """
        service = 'supervisor_notification'
        subscribers_key = f'OML:SUPERVISION:SUBSCRIBERS:{service}'
        suscribers = self.redis_connection.smembers(subscribers_key)
        # Use redis pipe?
        for suscriber in suscribers:
            self.notify(suscriber, payload)

    def notify_transcription_status(self, callid, status, file='', msg=''):
        payload = {
            "type": "speech_analytics",
            "task": "transcription",
            "callid": callid,
            "transcription_status": status,
            "transcription_file": file,
        }
        if msg:
            payload['msg'] = msg
        self.notify_speech_analytics(payload)


class SpeechAnalysisStatusUpdater():

    def update_transcription(self, callid, status, transcription_file='', msg=''):
        callid_normalized = normalize_callid_for_db(callid)
        self._update_transcription_sql(callid_normalized, status, transcription_file)
        notifyer = DjangoChannelsNotifyer()
        notifyer.notify_transcription_status(callid_normalized, status, transcription_file, msg)

    def _update_transcription_sql(self, callid: str, status: int, transcription_file: str | None = None):
        """Upsert SpeechAnalysis.transcription_status."""
        params = {
            "callid": callid,
            "t_status": status,
            "t_file": transcription_file,
            "s_status": 0,   # EMPTY
            "qa_status": 0,  # EMPTY
        }
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO reportes_app_speechanalysis
                        (callid, transcription_status, transcription_file,
                         sentiment_status, qa_status, time)
                    VALUES (%(callid)s, %(t_status)s, %(t_file)s,
                            %(s_status)s, %(qa_status)s, NOW())
                    ON CONFLICT (callid)
                    DO UPDATE SET
                        transcription_status = EXCLUDED.transcription_status,
                        transcription_file = EXCLUDED.transcription_file,
                        time = NOW();
                    """,
                    params,
                )

    def update_sentiment_sql(callid: str, status: int, sentiment_file: str | None = None):
        """Update SpeechAnalysis.sentiment_status."""
        params = {
            "callid": callid,
            "status": status,
            "sfile": sentiment_file,
        }
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE reportes_app_speechanalysis
                    SET sentiment_status = %(status)s,
                        sentiment_file = %(sfile)s,
                        time = NOW()
                    WHERE callid = %(callid)s;
                    """,
                    params,
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"SpeechAnalysis with callid={callid!r} does not exist")

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"sentiment_{callid}",
            {
                "type": "notify",
                "payload": {
                    "callid": callid,
                    "sentiment_status": status,
                    "sentiment_file": sentiment_file,
                },
            },
        )
