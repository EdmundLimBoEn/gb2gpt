#!/usr/bin/env python3
"""Isolated real-HTTP smoke; no credentials/fleet needed and no outbound wake."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, BRIDGE_TOKEN=secrets.token_urlsafe(32), OAUTH_CLIENT_SECRET=secrets.token_urlsafe(32), SMOKE_BOT_TOKEN=secrets.token_urlsafe(32), WAKE_ENABLED='false')
    config = {'fleet_name': 'Smoke', 'public_url': base, 'oauth_redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect'], 'bots': [{'id': 'smoke', 'name': 'Smoke bot', 'token_env': 'SMOKE_BOT_TOKEN'}]}
    Path(tmp, 'fleet.json').write_text(json.dumps(config))
    proc = subprocess.Popen([sys.executable, str(root / 'bridge.py'), '--config', str(Path(tmp, 'fleet.json')), '--db', str(Path(tmp, 'jobs.sqlite3')), '--env-file', '/dev/null', '--port', str(port)], env=env, stdout=subprocess.DEVNULL)
    def call(tool, args, token):
        request = Request(base + '/mcp', data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}).encode(), headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'Authorization': 'Bearer ' + token})
        with urlopen(request, timeout=3) as response:
            value = json.load(response)['result']
        assert not value.get('isError'), value
        return value['structuredContent']
    try:
        for _ in range(50):
            if proc.poll() is not None:
                raise RuntimeError('Bridge exited during startup')
            try:
                with urlopen(base + '/health', timeout=1) as response:
                    assert json.load(response)['ok']
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Health check timed out')
        print('PASS health over HTTP')
        out = call('create_job', {'conversation_id': 'smoke', 'request_id': 'smoke-one', 'message': 'dry-run only', 'bot_id': 'smoke', 'dry_run': True}, env['BRIDGE_TOKEN'])
        assert out['dry_run'] and not out['persisted'] and not out['wake_attempted']
        assert call('claim_job', {}, env['SMOKE_BOT_TOKEN'])['job'] is None
        print('PASS MCP dry-run create_job; no persisted job, no wake, worker queue empty')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
