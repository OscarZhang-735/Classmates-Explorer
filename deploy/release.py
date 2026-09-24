"""Linux-only, single-host releases. Never deletes files, images, or volumes."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
import uuid


PROJECT = 'classmates-explorer'


class Runtime:
    def run(self, args, env=None, timeout=120):
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            # Compose output can contain interpolated configuration. Do not log it.
            raise RuntimeError('Command failed: ' + args[0])
        return result.stdout.strip()

    def request(self, url, origin=None, timeout=5):
        request = Request(url, headers={'Origin': origin} if origin else {})
        with urlopen(request, timeout=timeout) as response:
            return json.load(response), response.headers

    def ready(self, compose, api_base, origin, deadline):
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError('Readiness deadline reached')
            return min(5, value)

        while time.monotonic() < deadline:
            try:
                ids = compose('ps', '-q', 'api', 'monitor', timeout=remaining()).splitlines()
                if len(ids) != 2:
                    raise RuntimeError('Missing services')
                states = json.loads(self.run(['docker', 'inspect', *ids], timeout=remaining()))
                if not all(s['State']['Running'] for s in states):
                    raise RuntimeError('Stopped service')
                api = next(s for s in states if s['Config']['Labels']['com.docker.compose.service'] == 'api')
                if api['State'].get('Health', {}).get('Status') != 'healthy':
                    raise RuntimeError('Unhealthy API')
                for base in ('http://127.0.0.1:18765', api_base):
                    body, _ = self.request(base + '/healthz', timeout=remaining())
                    if body != {'status': 'ok'}:
                        raise RuntimeError('Health response mismatch')
                body, headers = self.request(api_base + '/api/config', origin, timeout=remaining())
                if body.get('mode') != 'remote' or headers.get('Access-Control-Allow-Origin') != origin:
                    raise RuntimeError('Remote mode or CORS mismatch')
                return
            except Exception:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        raise RuntimeError('Readiness checks timed out')


@contextmanager
def deployment_lock(root):
    import fcntl
    with (root / '.release.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another deployment is running') from None
        yield


def origin(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Expected an HTTPS origin without a path')
    return value


def deploy(root, source, image, api_base, pages_origin, runtime=None):
    runtime = runtime or Runtime()
    root, source = Path(root).resolve(), Path(source).resolve()
    if not re.fullmatch(r'ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}', image):
        raise ValueError('A GHCR image digest is required')
    origin(api_base)
    origin(pages_origin)
    if not (root / '.env.remote').is_file():
        raise RuntimeError('Initialize the server and .env.remote first')
    with deployment_lock(root):
        return release_locked(root, source, image, api_base, pages_origin, runtime)


def release_locked(root, source, image, api_base, pages_origin, runtime):
    state_file = root / 'current-release.json'
    old_files = json.loads(state_file.read_text())['files'] if state_file.exists() else [str(root / 'compose.yaml')]
    release = root / 'releases' / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12])
    release.mkdir(parents=True)
    shutil.copyfile(source, release / 'compose.yaml')
    shutil.copyfile(Path(__file__), release / 'release.py')
    env = {**os.environ, 'APP_IMAGE': image}

    def compose(files, *args, timeout=120):
        command = ['docker', 'compose', '--project-name', PROJECT,
                   '--project-directory', str(root), '--env-file', str(root / '.env.remote')]
        for file in files:
            command += ['-f', str(file)]
        return runtime.run(command + list(args), env=env, timeout=timeout)

    # Read normalized configuration in memory; it contains secrets and is not saved.
    old_config = json.loads(compose(old_files, 'config', '--format', 'json'))
    new_files = [str(release / 'compose.yaml')]
    new_config = json.loads(compose(new_files, 'config', '--format', 'json'))
    for key in ('volumes', 'networks'):
        if old_config.get(key) != new_config.get(key):
            raise RuntimeError('Volume/network changes require a separate migration')
    previous = {}
    for service in ('api', 'monitor'):
        container = compose(old_files, 'ps', '-q', service)
        if not container or '\n' in container:
            raise RuntimeError('Expected one existing container per service')
        metadata = json.loads(runtime.run(['docker', 'inspect', container]))[0]
        if metadata['Config']['Labels'].get('com.docker.compose.project') != PROJECT:
            raise RuntimeError('Unexpected Compose project')
        if not metadata['State']['Running']:
            raise RuntimeError('Existing service is not running')
        if service == 'api':
            volume_name = old_config['volumes']['explorer-data']['name']
            if not any(m.get('Name') == volume_name and m['Destination'] == '/data' for m in metadata['Mounts']):
                raise RuntimeError('Existing database volume does not match configuration')
        previous[service] = {'image': metadata['Image']}
    # Snapshot the exact old files and pin both service images for rollback.
    rollback_files = []
    for index, file in enumerate(old_files):
        target = release / f'previous-{index}.yaml'
        shutil.copyfile(file, target)
        rollback_files.append(str(target))
    override = release / 'previous-images.json'
    override.write_text(json.dumps({'services': previous}), encoding='utf-8')
    rollback_files.append(str(override))
    runtime.run(['docker', 'pull', image])
    backup = f'/data/backups/before-{release.name}.sqlite3'
    compose(old_files, 'exec', '-T', 'api', 'python', 'deploy/backup.py', backup)
    (release / 'metadata.json').write_text(json.dumps({'image': image, 'backup': backup,
                                                     'rollback_files': rollback_files}), encoding='utf-8')
    # Pin the candidate too: future runs must not depend on .env.remote APP_IMAGE.
    candidate = release / 'image.json'
    candidate.write_text(json.dumps({'services': {s: {'image': image} for s in previous}}), encoding='utf-8')
    new_files.append(str(candidate))
    try:
        deadline = time.monotonic() + 120
        compose(new_files, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '90', 'api', 'monitor', timeout=95)
        runtime.ready(lambda *a, **kw: compose(new_files, *a, **kw), api_base, pages_origin, deadline)
        pending = release / 'current.json'
        pending.write_text(json.dumps({'files': new_files, 'image': image}), encoding='utf-8')
        os.replace(pending, state_file)
    except BaseException as failure:
        try:
            deadline = time.monotonic() + 120
            compose(rollback_files, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '90', 'api', 'monitor', timeout=95)
            runtime.ready(lambda *a, **kw: compose(rollback_files, *a, **kw), api_base, pages_origin, deadline)
            pending = release / 'rolled-back.json'
            pending.write_text(json.dumps({'files': rollback_files}), encoding='utf-8')
            os.replace(pending, state_file)
        except BaseException:
            raise RuntimeError(f'Deployment and rollback failed; inspect {release}') from None
        raise RuntimeError(f'Deployment failed; previous version restored. Records: {release}') from failure
    return release


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/opt/classmates-explorer')
    parser.add_argument('--compose', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--api-base', required=True)
    parser.add_argument('--pages-origin', required=True)
    args = parser.parse_args()
    try:
        print(deploy(args.root, args.compose, args.image, args.api_base, args.pages_origin))
    except Exception as error:
        raise SystemExit(str(error)) from None
