"""
ASGI config for Fastagi project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.2/howto/deployment/asgi/
"""
import os


from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from channels.security.websocket import AllowedHostsOriginValidator
from django.urls import path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Fastagi.settings')

django_asgi_app = get_asgi_application()

from ami_app.websocket import WebsocketConsumer # noqa 

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        URLRouter([
            path("ws/", WebsocketConsumer.as_asgi()),
        ])
    )
})
