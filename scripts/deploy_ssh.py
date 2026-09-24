"""Upload a release bundle and invoke the server deployer with strict SSH identity checks."""
import argparse
from pathlib import Path
import shlex
import subprocess
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('host', 'user', 'key', 'known-hosts', 'image', 'api-base', 'pages-origin'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--port', type=int, default=22)
    p.add_argument('--root', default='/opt/classmates-explorer')
    args = p.parse_args()
    if args.host.startswith('-') or args.user.startswith('-') or not args.root.startswith('/'):
        p.error('Invalid SSH destination or deployment root')
    if not Path(args.known_hosts).is_file() or not Path(args.key).is_file():
        p.error('Provide an existing private key and verified known_hosts file')
    options = ['-i', args.key, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(Path(args.known_hosts).resolve()),
               '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
    destination = args.user + '@' + args.host
    inbox = args.root.rstrip('/') + '/incoming/' + uuid.uuid4().hex
    ssh = ['ssh', *options, '-p', str(args.port), destination]
    subprocess.run([*ssh, 'mkdir -p -- ' + shlex.quote(inbox)], check=True, timeout=30)
    repo = Path(__file__).resolve().parents[1]
    # Stream only two allowlisted files. No env files, credentials or database.
    for local, name in ((repo / 'compose.yaml', 'compose.yaml'), (repo / 'deploy/release.py', 'release.py')):
        with local.open('rb') as content:
            subprocess.run([*ssh, 'umask 077; cat > ' + shlex.quote(inbox + '/' + name)],
                           stdin=content, check=True, timeout=60)
    command = ['python3', inbox + '/release.py', '--root', args.root,
               '--compose', inbox + '/compose.yaml', '--image', args.image,
               '--api-base', args.api_base, '--pages-origin', args.pages_origin]
    # No runner-side timeout: do not interrupt a server-side rollback in progress.
    subprocess.run([*ssh, shlex.join(command)], check=True)


if __name__ == '__main__':
    main()
