import asyncari
from asyncari.state import ToplevelChannelState, DTMFHandler
import anyio
import logging
import asks

import os
ast_host = os.getenv("AST_HOST", 'asterisk')
ast_port = int(os.getenv("AST_ARI_PORT", 8088))
ast_url = os.getenv("AST_URL", f'http://{ast_host}:{ast_port}/')
ast_username = os.getenv("AST_USER", 'astuser2')
ast_password = os.getenv("AST_PASS", 'asterisk')
ast_app = os.getenv("AST_APP", 'apptest')


class State(ToplevelChannelState, DTMFHandler):
    do_hang = False

    async def on_start(self):
        await self.channel.play(media='sound:hello-world')

    async def on_dtmf_star(self, evt):
        self.do_hang = True
        await self.channel.play(media='sound:vm-goodbye')

    async def on_dtmf_pound(self, evt):
        await self.channel.play(media='sound:asterisk-friend')

    async def on_dtmf(self, evt):
        await self.channel.play(media='sound:digits/%s' % evt.digit)

    async def on_playback_finished(self, evt):
        if self.do_hang:
            try:
                await self.channel.continueInDialplan()
            except asks.errors.BadStatus:
                pass


async def on_start(client):

    async with client.on_channel_event('StasisStart') as listener:
        async for objs, event in listener:
            channel = objs['channel']
            await channel.answer()
            client.taskgroup.start_soon(State(channel).start_task)


async def main():
    async with asyncari.connect(
        ast_url, ast_app, ast_username, ast_password
    ) as client:
        client.taskgroup.start_soon(on_start, client)
        # Run the WebSocket
        async for m in client:
            pass
            # print("** EVENT **", m)

if __name__ == "__main__":
    try:
        anyio.run(main, backend="trio")
    except KeyboardInterrupt:
        pass
