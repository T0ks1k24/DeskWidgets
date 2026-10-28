"""Now playing on Windows: the system media controls (the same session the volume flyout shows).

Needs the optional `winsdk` package (`pip install winsdk`); without it the widget just shows
that nothing is playing. The player is polled once a second: cheap, and robust against
players that send no change notifications.
"""
import asyncio
import hashlib
import threading
import time

from ..core import paths
from ..core.util import clean, log
from .base import Provider

PLAYING, PAUSED = 4, 5   # GlobalSystemMediaTransportControlsSessionPlaybackStatus


class Media(Provider):
    name = 'media'

    def __init__(self):
        super().__init__()
        self._loop = None
        self._thread = None
        self._session = None
        self._art_key = None
        self._art_path = ''

    def start(self):
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager  # noqa: F401
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name='provider-media', daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def control(self, command):
        actions = {'PlayPause': 'try_toggle_play_pause_async', 'Next': 'try_skip_next_async',
                   'Previous': 'try_skip_previous_async', 'Play': 'try_play_async', 'Pause': 'try_pause_async',
                   'Stop': 'try_stop_async'}
        action = actions.get(command)
        session = self._session
        if not action or session is None or self._loop is None:
            return False
        asyncio.run_coroutine_threadsafe(getattr(session, action)(), self._loop)
        return True

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._watch())
        self._loop.run_forever()

    async def _watch(self):
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        manager = await Manager.request_async()
        while True:
            try:
                await self._read(manager)
            except Exception as e:   # the session can vanish between two calls
                log(f'media: {e}')
                self.publish({'status': 'none'})
            await asyncio.sleep(1.0)

    async def _read(self, manager):
        session = manager.get_current_session()
        self._session = session
        if session is None:
            self.publish({'status': 'none'})
            return
        playback = session.get_playback_info()
        status = {PLAYING: 'Playing', PAUSED: 'Paused'}.get(int(playback.playback_status))
        props = await session.try_get_media_properties_async()
        if status is None or not props.title:
            self.publish({'status': 'none'})
            return
        timeline = session.get_timeline_properties()
        end = timeline.end_time.total_seconds() - timeline.start_time.total_seconds()
        self.publish({
            'status': status, 'player': _player_name(session.source_app_user_model_id),
            'title': clean(props.title), 'artist': clean(props.artist), 'album': clean(props.album_title),
            'length': max(end, 0.0), 'pos': timeline.position.total_seconds(),
            'ts': timeline.last_updated_time.timestamp() if hasattr(timeline.last_updated_time, 'timestamp') else time.time(),
            'rate': float(playback.playback_rate or 1.0), 'art': await self._art(props),
        })

    async def _art(self, props):
        key = (props.title, props.artist)
        if key == self._art_key:
            return self._art_path
        self._art_key, self._art_path = key, ''
        if props.thumbnail is None:
            return ''
        from winsdk.windows.storage.streams import DataReader
        stream = await props.thumbnail.open_read_async()
        if not stream.size:
            return ''
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(stream.size)
        buffer = bytearray(stream.size)
        reader.read_bytes(buffer)
        out = paths.IMAGES / ('art-' + hashlib.sha1(repr(key).encode()).hexdigest()[:16])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes(buffer))
        self._art_path = str(out)
        return self._art_path


def _player_name(app_id):
    """'Spotify.exe' or 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic' -> a readable name."""
    name = (app_id or '').split('!')[0].split('_')[0].removesuffix('.exe')
    return name.split('.')[-1] if name else ''
