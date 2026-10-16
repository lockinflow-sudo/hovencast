"""Install the bundled native AirPlay helper and its corresponding source."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil


def main():
    root = Path(__file__).resolve().parents[1] / 'native'
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64','amd64'):
        raise SystemExit('Build the native helper for this architecture using native/README.md.')
    manifest = json.loads((root / 'provenance.json').read_text())
    binary = root / 'linux-amd64/doubletake'
    if hashlib.sha256(binary.read_bytes()).hexdigest() != manifest['binary_sha256']:
        raise SystemExit('Native AirPlay helper checksum mismatch.')
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    target = data / 'hovencast/native'
    (target / 'bin').mkdir(parents=True,exist_ok=True)
    temporary = target / 'bin/.doubletake-new'
    shutil.copy2(binary,temporary)
    temporary.chmod(0o755)
    os.replace(temporary,target/'bin/doubletake')
    for name in ('doubletake-source.tar.gz','LICENSE','COPYING.GPL','README.md','provenance.json'):
        shutil.copy2(root/name,target/name)
    print('Native AirPlay helper installed.')


if __name__ == '__main__':
    main()
