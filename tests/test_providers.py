import unittest
from unittest.mock import Mock, patch

from deskwidgets import providers
from deskwidgets.providers import batteries, docker, hid_mice, media_mpris
from deskwidgets.providers.base import PollingProvider

UPOWER = '''Device: /org/freedesktop/UPower/devices/line_power_AC
  native-path:          AC
  power supply:         yes
  online:               yes

Device: /org/freedesktop/UPower/devices/mouse_hidpp_battery_0
  native-path:          hidpp_battery_0
  model:                MX Master 3
  power supply:         no
  updated:              Wed 28 Oct 2026 15:00:00 (10 seconds ago)
  has history:          yes
  mouse
    present:             yes
    rechargeable:        yes
    state:               charging
    warning-level:       none
    percentage:          75%
    icon-name:          'battery-good-charging-symbolic'

Device: /org/freedesktop/UPower/devices/DisplayDevice
  power supply:         yes
  percentage:          96%
'''


class DockerTests(unittest.TestCase):
    def test_parses_containers(self):
        provider = docker.Docker()
        with patch.object(docker, 'run_command', return_value=(0, 'web|nginx:1|running|Up 2 hours\ndb|pg|exited|Exited (0) 1 day ago\nbroken line\n')):
            provider.poll()
        data = provider.snapshot()
        self.assertTrue(data['available'])
        self.assertEqual([c['name'] for c in data['containers']], ['web', 'db'])
        self.assertEqual(data['containers'][1]['state'], 'exited')

    def test_unavailable_when_docker_fails_or_is_missing(self):
        provider = docker.Docker()
        for result in ((1, ''), (None, '')):
            with patch.object(docker, 'run_command', return_value=result):
                provider.poll()
            self.assertEqual(provider.snapshot(), {'available': False, 'containers': []})


class BatteryTests(unittest.TestCase):
    def test_upower_skips_power_supplies_and_the_display_device(self):
        with patch.object(batteries, 'run_command', return_value=(0, UPOWER)):
            devices = batteries.read_upower()
        self.assertEqual(devices, [{'name': 'MX Master 3', 'percent': 75.0, 'charging': True, 'online': True, 'kind': 'mouse'}])

    def test_no_upower_means_no_devices(self):
        with patch.object(batteries, 'run_command', return_value=(None, '')):
            self.assertEqual(batteries.read_upower(), [])

    def test_hid_mouse_that_falls_asleep_keeps_its_last_charge(self):
        provider = batteries.Batteries()
        device = {'node': '/dev/hidraw9', 'name': 'Pulsar', 'access': True}
        with patch.object(batteries.hid_mice, 'find', return_value=[device]), \
                patch.object(batteries, 'read_upower', return_value=[]), \
                patch.object(batteries.sys, 'platform', 'linux'):
            with patch.object(batteries.hid_mice, 'read', return_value={'online': True, 'pct': 80, 'charging': False}):
                provider.poll()
            self.assertEqual(provider.snapshot()['devices'][0]['percent'], 80)
            with patch.object(batteries.hid_mice, 'read', return_value={'online': False, 'pct': None, 'charging': False}):
                provider.poll()
            slept = provider.snapshot()['devices'][0]
            self.assertEqual((slept['percent'], slept['online']), (80, False))

    def test_unreadable_mouse_is_reported_as_denied(self):
        provider = batteries.Batteries()
        with patch.object(batteries.hid_mice, 'find', return_value=[{'node': 'x', 'name': 'Kysona', 'access': False}]), \
                patch.object(batteries, 'read_upower', return_value=[]), patch.object(batteries.sys, 'platform', 'linux'):
            provider.poll()
        self.assertEqual(provider.snapshot()['denied'], 'Kysona')

    def test_hid_protocol_helpers(self):
        packet = hid_mice._packet(hid_mice.CMD_POWER)
        self.assertEqual(len(packet), hid_mice.LENGTH)
        self.assertEqual(sum(packet[:16]) + packet[16] & 0xff, 0x55)
        self.assertEqual(hid_mice.pretty_name('Compx  Pulsar X2 Wireless'), 'Pulsar X2')


class MediaTests(unittest.TestCase):
    def test_resume_excludes_time_spent_paused(self):
        state = {'status': 'Playing', 'pos': 10, 'ts': 100, 'rate': 1, 'meta': {}}
        with patch('deskwidgets.providers.media_mpris.time.time', return_value=105):
            media_mpris.Media._apply(state, {'PlaybackStatus': 'Paused'})
        with patch('deskwidgets.providers.media_mpris.time.time', return_value=200):
            media_mpris.Media._apply(state, {'PlaybackStatus': 'Playing'})
        self.assertEqual((state['pos'], state['ts']), (15, 200))

    def test_rate_change_preserves_elapsed_position(self):
        state = {'status': 'Playing', 'pos': 10, 'ts': 100, 'rate': 1, 'meta': {}}
        with patch('deskwidgets.providers.media_mpris.time.time', return_value=105):
            media_mpris.Media._apply(state, {'Rate': 2})
        self.assertEqual((state['pos'], state['ts'], state['rate']), (15, 105, 2))

    def test_new_track_restarts_the_position(self):
        state = {'status': 'Playing', 'pos': 90, 'ts': 100, 'rate': 1, 'meta': {'xesam:title': 'old'}}
        with patch('deskwidgets.providers.media_mpris.time.time', return_value=130):
            media_mpris.Media._apply(state, {'Metadata': {'xesam:title': 'new'}})
        self.assertEqual((state['pos'], state['ts']), (0.0, 130))

    def test_player_names(self):
        self.assertEqual(media_mpris.player_name('Mozilla firefox_firefox'), 'Firefox')
        self.assertEqual(media_mpris.player_name('spotify'), 'Spotify')


class HubTests(unittest.TestCase):
    def fake(self, name):
        provider = Mock(spec=PollingProvider)
        provider.name = name
        provider.snapshot.return_value = {'x': 1}
        provider.control.return_value = True
        return provider

    def test_providers_start_once_and_stop_when_unused(self):
        made = {}
        with patch.object(providers, '_create', side_effect=lambda n: made.setdefault(n, self.fake(n))):
            hub = providers.ServiceHub()
            hub.sync({'docker'})
            hub.sync({'docker', 'media'})
            made['docker'].start.assert_called_once()
            self.assertEqual(hub.snapshot('media'), {'x': 1})
            self.assertTrue(hub.media_control('Next'))
            hub.sync({'media'})
            made['docker'].stop.assert_called_once()
            self.assertIsNone(hub.snapshot('docker'))
            hub.stop()
            made['media'].stop.assert_called_once()

    def test_a_broken_provider_is_skipped_and_not_retried(self):
        with patch.object(providers, '_create', side_effect=RuntimeError('no D-Bus')) as create, \
                patch.object(providers, 'log') as log:
            hub = providers.ServiceHub()
            hub.sync({'media'})
            hub.sync({'media'})
            self.assertEqual(create.call_count, 1)
            log.assert_called_once()
            self.assertIsNone(hub.snapshot('media'))
            self.assertFalse(hub.media_control('PlayPause'))

    def test_unknown_service_names_are_ignored(self):
        hub = providers.ServiceHub()
        hub.sync({'no-such-service'})
        self.assertIsNone(hub.snapshot('no-such-service'))

    def test_polling_provider_survives_exceptions_and_stops(self):
        class Flaky(PollingProvider):
            name = 'flaky'
            every = 0.01
            calls = 0

            def poll(self):
                Flaky.calls += 1
                if Flaky.calls == 1:
                    raise OSError('first poll fails')
                self.publish(Flaky.calls)

        provider = Flaky()
        with patch('deskwidgets.providers.base.log'):
            provider.start()
            deadline = __import__('time').monotonic() + 3
            while provider.snapshot() is None and __import__('time').monotonic() < deadline:
                __import__('time').sleep(0.01)
            provider.stop()
        self.assertIsNotNone(provider.snapshot())


if __name__ == '__main__':
    unittest.main()
