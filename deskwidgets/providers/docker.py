"""Docker containers for the Docker widget: one bounded `docker ps` shared by every copy of it."""
from .base import PollingProvider, run_command

FORMAT = '{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}'


class Docker(PollingProvider):
    name = 'docker'
    every = 3.0

    def poll(self):
        code, output = run_command(['docker', 'ps', '-a', '--format', FORMAT], timeout=5)
        if code != 0:
            self.publish({'available': False, 'containers': []})
            return
        containers = []
        for line in output.splitlines():
            fields = line.split('|')
            if len(fields) == 4:
                containers.append(dict(zip(('name', 'image', 'state', 'status'), fields)))
        self.publish({'available': True, 'containers': containers})
