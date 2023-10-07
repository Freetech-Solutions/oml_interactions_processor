import asyncio
import asyncari

async def on_stasis_start(obj, event):
    """Manipulador para el evento StasisStart."""
    channel = event['channel']
    print(f"Llamada recibida de: {channel['caller']['number']}")

    # Ejecutar un playback en el canal
    await obj.channels.play(channelId=channel['id'], media='sound:hello-world')

    # Colgar el canal después del playback
    await asyncio.sleep(2)  # Esperar un poco para que el playback se complete
    await obj.channels.hangup(channelId=channel['id'])

async def on_startup(ari):
    """Se ejecuta después de conectar a ARI."""
    print("Conectado a ARI")

    # Suscribirse a los eventos relevantes
    ari.on_channel_event('StasisStart', on_stasis_start)

    # Suscribirse a nuestra aplicación
    await ari.applications.subscribe(applicationName='my_ari_app', eventSource='channel:')

async def main():
    async with asyncari.connect(
        'http://acd:7088/ari',
        'my_ari_app',
        'omnileads',
        '5_MeO_DMT',
    ) as ari:
        await on_startup(ari)
        await asyncio.sleep(30)  # Mantener la aplicación en ejecución por un tiempo para observar eventos.

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
