"""Verify and install the bundled Open Screen sender without elevated privileges."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def install(root=None, data=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1] / 'native/chromecast'
    data = Path(data) if data else Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise RuntimeError('Build the Google Cast helper for this architecture; see native/chromecast/README.md.')
    manifest = json.loads((root / 'provenance.json').read_text())
    binary = root / 'linux-amd64/cast_sender'
    if hashlib.sha256(binary.read_bytes()).hexdigest() != manifest['binary_sha256']:
        raise RuntimeError('Native Google Cast helper checksum mismatch.')
    dependencies = subprocess.run(['ldd', str(binary)], capture_output=True, text=True, check=False)
    if dependencies.returncode or 'not found' in dependencies.stdout:
        raise RuntimeError('Google Cast helper requires matching system libraries. Update Arch or rebuild using native/chromecast/README.md.\n' + dependencies.stdout + dependencies.stderr)
    target = data / 'hovencast'
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / '.cast_sender-new'
    try:
        shutil.copy2(binary, temporary)
        temporary.chmod(0o755)
        os.replace(temporary, target / 'cast_sender')
    finally:
        temporary.unlink(missing_ok=True)
    for source, name in [('provenance.json', 'cast-sender-provenance.json'),
                         ('source.patch', 'cast-sender-source.patch'), ('args.gn', 'cast-sender-args.gn')]:
        shutil.copy2(root / source, target / name)
    shutil.copytree(root / 'licenses', target / 'cast-sender-licenses', dirs_exist_ok=True)
    print('Native Google Cast helper installed.')


if __name__ == '__main__':
    try:
        install()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error))
