#!/usr/bin/env python3
"""Generate local secrets without printing or overwriting them."""
import sys
if len(sys.argv) > 1:
    raise SystemExit(__doc__)
import json
import os
from pathlib import Path
import re
import secrets
import shutil

root = Path(__file__).resolve().parents[1]
fleet = root / 'fleet.json'
if not fleet.exists():
    shutil.copyfile(root / 'fleet.example.json', fleet)
config = json.loads(fleet.read_text())
names = ['BRIDGE_TOKEN', 'OAUTH_CLIENT_SECRET'] + [b['token_env'] for b in config['bots']]
if any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', n) for n in names):
    raise SystemExit('Invalid token_env in fleet.json')
try:
    fd = os.open(root / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit('.env already exists; left unchanged. Edit it locally if adding bots.')
with os.fdopen(fd, 'w') as f:
    for name in dict.fromkeys(names):
        f.write(f'{name}={secrets.token_urlsafe(32)}\n')
    f.write('WAKE_ENABLED=false\n')
print('Created .env (mode 0600) and fleet.json. Secrets were not printed.')
