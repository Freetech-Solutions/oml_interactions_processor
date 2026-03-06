"""
Minimal settings for a Channels publisher container.
"""
import os

SECRET_KEY = 's1+*bfrvb@=k@c&9=pm!0sijjewneu5p5rojil#q+!a2y&as-4'
# SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dummy-key-for-publisher")
DEBUG = False
TIME_ZONE = "UTC"
USE_TZ = True

INSTALLED_APPS = [
    "channels",
]

# -- Required ENV Vars -- #
REDIS_HOSTNAME = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
POSTGRES_HOST = os.getenv('PGHOST')
POSTGRES_PORT = os.getenv('PGPORT')
POSTGRES_DATABASE = os.getenv('PGDATABASE')
POSTGRES_USER = os.getenv('PGUSER')
POSTGRES_OML_PASSWORD = os.getenv('POSTGRES_OML_PASSWORD')
DATABASE_REPLICA_ENABLED = os.getenv("PGHOSTHA") == "True"
DATABASE_REPLICA_HOST = os.getenv("PGHOSTRO")

# Channel layer configuration (host/port format; "address" with tuple causes decode error in channels_redis).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [{"host": REDIS_HOSTNAME, "port": REDIS_PORT, "db": 4}],
            "prefix": "",
            "expiry": 120,
            "group_expiry": 86400,
            "capacity": 500,
        },
    },
}

# Minimal database to satisfy Django setup when apps query connections
# (usually unused by the publisher). Replace with your real DB only if needed.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'HOST': POSTGRES_HOST,
        'PORT': POSTGRES_PORT,
        'NAME': POSTGRES_DATABASE,
        'USER': POSTGRES_USER,
        'PASSWORD': POSTGRES_OML_PASSWORD,
        'CONN_MAX_AGE': 300,
        'ATOMIC_REQUESTS': True,
    },
    'replica': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'HOST': DATABASE_REPLICA_HOST if DATABASE_REPLICA_ENABLED else POSTGRES_HOST,
        'PORT': POSTGRES_PORT,
        'NAME': POSTGRES_DATABASE,
        'USER': POSTGRES_USER,
        'PASSWORD': POSTGRES_OML_PASSWORD,
        'CONN_MAX_AGE': 300,
        'ATOMIC_REQUESTS': True,
    }
}

# Silence system checks unrelated to the publisher context.
SILENCED_SYSTEM_CHECKS = [
    "staticfiles.W004",  # STATICFILES_DIRS not set
]
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
