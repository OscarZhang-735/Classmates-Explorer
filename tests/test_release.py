import json
from pathlib import Path

import pytest

from deploy.release import release_locked


IMAGE = 'ghcr.io/example/app@sha256:' + 'a' * 64


class FakeDocker:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.checks = 0

    def run(self, args, env=None, timeout=120):
        self.calls.append(args)
        if 'config' in args:
            return json.dumps({'volumes': {'explorer-data': {'name': 'classmates-explorer_explorer-data'}},
                               'networks': {'backend': {'name': 'classmates-explorer_backend'}}})
        if 'ps' in args:
            return args[-1]
        if 'inspect' in args:
            return json.dumps([{'Image': 'sha256:old-' + args[-1],
                                'Config': {'Labels': {'com.docker.compose.project': 'classmates-explorer'}},
                                'State': {'Running': True},
                                'Mounts': [{'Name': 'classmates-explorer_explorer-data', 'Destination': '/data'}]}])
        if 'exec' in args and self.failure == 'backup':
            raise RuntimeError('Backup failed')
        if 'up' in args and self.failure == 'start' and not any('previous-' in s for s in args):
            raise RuntimeError('Startup failed')
        return ''

    def ready(self, compose, api_base, origin, deadline):
        self.checks += 1
        if self.failure == 'health' and self.checks == 1 or self.failure == 'rollback':
            raise RuntimeError('Health check failed')


def setup(tmp_path):
    (tmp_path / '.env.remote').write_text('SESSION_SECRET=keep-this\n')
    (tmp_path / 'compose.yaml').write_text('services: {}\n')
    return tmp_path / 'compose.yaml'


@pytest.mark.parametrize('failure', [None, 'backup', 'start', 'health', 'rollback'])
def test_release_transaction(tmp_path, failure):
    source = setup(tmp_path)
    runtime = FakeDocker(failure)
    if failure:
        with pytest.raises(RuntimeError, match={'backup': 'Backup failed', 'rollback': 'rollback failed'}.get(failure, 'previous version restored')):
            release_locked(tmp_path, source, IMAGE, 'https://api.example.com', 'https://pages.example.com', runtime)
    else:
        release_locked(tmp_path, source, IMAGE, 'https://api.example.com', 'https://pages.example.com', runtime)
    assert (tmp_path / '.env.remote').read_text() == 'SESSION_SECRET=keep-this\n'
    ups = [c for c in runtime.calls if 'up' in c]
    assert len(ups) == (0 if failure == 'backup' else 1 if failure is None else 2)
    assert all('--no-build' in c and '--pull' in c and 'never' in c for c in ups)
    assert not any('down' in c or 'rm' in c or 'prune' in c for c in runtime.calls)
    state = tmp_path / 'current-release.json'
    if failure in ('backup', 'rollback'):
        assert not state.exists()
    else:
        files = json.loads(state.read_text())['files']
        saved = json.loads(Path(files[-1]).read_text())
        assert saved['services']['api']['image'] == (IMAGE if failure is None else 'sha256:old-api')
    records = list((tmp_path / 'releases').glob('*/previous-images.json'))
    assert len(records) == 1
    assert 'keep-this' not in ''.join(p.read_text() for p in (tmp_path / 'releases').rglob('*') if p.is_file())


def test_network_change_stops_before_backup_and_update(tmp_path):
    source = setup(tmp_path)
    runtime = FakeDocker()
    original = runtime.run
    count = 0

    def changed(args, **kwargs):
        nonlocal count
        output = original(args, **kwargs)
        if 'config' in args:
            count += 1
            if count == 2:
                data = json.loads(output)
                data['networks']['backend']['name'] = 'different-network'
                return json.dumps(data)
        return output

    runtime.run = changed
    with pytest.raises(RuntimeError, match='separate migration'):
        release_locked(tmp_path, source, IMAGE, 'https://api.example.com', 'https://pages.example.com', runtime)
    assert not any('exec' in c or 'up' in c for c in runtime.calls)


def test_server_lock_rejects_concurrent_deployment(tmp_path):
    pytest.importorskip('fcntl')
    from deploy.release import deployment_lock
    with deployment_lock(tmp_path):
        with pytest.raises(RuntimeError, match='Another deployment'):
            with deployment_lock(tmp_path):
                pytest.fail('Second deployment acquired the lock')
    with deployment_lock(tmp_path):
        pass
