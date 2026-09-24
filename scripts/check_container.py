"""Exercise an isolated CI container and SQLite volume; never use production data.

Containers are stopped, but containers/volumes are not deleted. GitHub's hosted
runner discards its VM after the job; local callers may inspect retained state.
"""
import argparse
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def docker(*args):
    result = subprocess.run(
        ['docker', *args], check=True, text=True, capture_output=True, timeout=120
    )
    return result.stdout.strip()


def wait_ready(base):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + '/healthz', timeout=3) as response:
                if json.load(response) == {'status': 'ok'}:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError('Container did not become healthy within 60 seconds')


def check(image):
    name = 'explorer-ci-' + uuid.uuid4().hex
    volume = name + '-data'
    docker('volume', 'create', volume)
    started = False
    try:
        docker('run', '-d', '--name', name, '--read-only', '--tmpfs', '/tmp',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '-p', '127.0.0.1::8765', '-v', volume + ':/data',
               '-e', 'APP_MODE=remote', '-e', 'GITHUB_TOKEN=',
               '-e', 'UNLIMITED_MODE=false',
               '-e', 'SESSION_SECRET=' + secrets.token_hex(32),
               '-e', 'ALLOWED_ORIGINS=["https://pages.example.com"]', image)
        started = True
        def base_url():
            binding = docker('port', name, '8765/tcp')
            return 'http://' + binding

        base = base_url()
        wait_ready(base)
        with urllib.request.urlopen(base + '/api/config', timeout=5) as response:
            config = json.load(response)
        assert config['mode'] == 'remote', config
        # A committed marker verifies that the same on-disk DB survives restart.
        docker('exec', name, 'python', '-c',
               "import sqlite3; c=sqlite3.connect('/data/remote.sqlite3'); "
               "c.execute('CREATE TABLE ci_probe (value TEXT)'); "
               "c.execute(\"INSERT INTO ci_probe VALUES ('persisted')\"); c.commit()")
        docker('restart', name)
        wait_ready(base_url())
        docker('exec', name, 'python', '-c',
               "import sqlite3; c=sqlite3.connect('/data/remote.sqlite3'); "
               "assert c.execute('SELECT value FROM ci_probe').fetchall()==[('persisted',)]; "
               "assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'")
        print('Container health, remote config, and SQLite restart checks passed.')
    except Exception:
        if started:
            print(docker('logs', name))
        raise
    finally:
        if started:
            docker('stop', name)
        print(f'Retained test container: {name}; volume: {volume}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    try:
        check(parser.parse_args().image)
    except subprocess.CalledProcessError as error:
        # Do not print the command: docker run includes the temporary secret.
        raise SystemExit(error.stderr or error.stdout or 'Docker command failed') from None
