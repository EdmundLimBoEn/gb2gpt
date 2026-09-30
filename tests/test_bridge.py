import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPCookieProcessor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import Bridge, Error, Server, NoRedirect, digest


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'BRIDGE_TOKEN': secrets.token_urlsafe(32), 'OAUTH_CLIENT_SECRET': secrets.token_urlsafe(32), 'BOT_A': secrets.token_urlsafe(32), 'BOT_B': secrets.token_urlsafe(32), 'WAKE_ENABLED': 'false'})
        self.env.start()
        self.config = {'fleet_name': 'Test fleet', 'public_url': 'http://127.0.0.1:8787', 'oauth_redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect'], 'bots': [{'id': 'a', 'name': 'A', 'token_env': 'BOT_A'}, {'id': 'b', 'name': 'B', 'token_env': 'BOT_B'}]}
        self.dbpath = str(Path(self.tmp.name, 'bridge.sqlite3'))
        self.app = Bridge(self.config, self.dbpath)
        self.server = Server(('127.0.0.1', 0), self.app)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.app.url = self.base
        self.app.resource = self.base + '/mcp'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.db.close()
        self.env.stop()
        self.tmp.cleanup()

    def call(self, name, args, role='owner'):
        return self.app.call(role, name, args)

    def create(self, request_id='r1', **kwargs):
        return self.call('create_job', dict(conversation_id='chat', request_id=request_id, message='hello', bot_id='a', **kwargs))

    def http(self, path, data=None, token=None, headers=None, form=False, opener=None):
        h = dict(headers or {})
        if token:
            h['Authorization'] = 'Bearer ' + token
        if data is not None:
            h['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
            data = (urlencode(data) if form else json.dumps(data)).encode()
        req = Request(self.base + path, data=data, headers=h)
        try:
            resp = (opener or build_opener(NoRedirect())).open(req, timeout=3)
        except HTTPError as e:
            resp = e
        with resp:
            raw = resp.read().decode()
            return resp.status, resp.headers, json.loads(raw) if raw and 'application/json' in resp.headers.get('Content-Type', '') else raw

    def test_discovery_routing_override_and_memory_honesty(self):
        with patch('bridge.secrets.choice', return_value='b') as choice:
            out = self.call('discover_hub', {'conversation_id': 'chat', 'request_id': 'discover'})
            choice.assert_called_once_with(['a', 'b'])
        job = out['job']
        self.assertEqual(job['bot_id'], 'b')
        self.assertEqual(job['status'], 'queued')
        claim = self.call('claim_job', {}, 'b')
        self.call('report_job', {'job_id': job['id'], 'claim_token': claim['claim_token'], 'status': 'succeeded', 'result': 'A is chief of staff', 'hub_bot_id': 'a'}, 'b')
        bound = self.call('bind_hub', {'conversation_id': 'chat', 'discovery_job_id': job['id']})
        self.assertFalse(bound['chatgpt_memory_saved'])
        for rid, bid in [('normal', None), ('override', 'b'), ('normal2', None)]:
            args = {'conversation_id': 'chat', 'request_id': rid, 'message': 'next'}
            if bid:
                args['bot_id'] = bid
            self.assertEqual(self.call('create_job', args)['job']['bot_id'], bid or 'a')

    def test_dry_run_no_persistence_or_network(self):
        self.app.wake_enabled = True
        with patch('bridge.build_opener') as opener:
            out = self.create(dry_run=True)
            self.assertFalse(out['persisted'])
            self.assertFalse(out['wake_attempted'])
            opener.assert_not_called()
        self.assertEqual(self.app.run('SELECT count(*) FROM jobs').fetchone()[0], 0)
        self.assertEqual(self.app.run('SELECT count(*) FROM conversations').fetchone()[0], 0)

    def test_retries_idempotent_and_conflicts(self):
        one = self.create()['job']['id']
        self.assertEqual(self.create()['job']['id'], one)
        with self.assertRaises(Error):
            self.call('create_job', {'conversation_id': 'chat', 'request_id': 'r1', 'message': 'changed', 'bot_id': 'a'})

    def test_atomic_claims_and_expired_claim_recovery(self):
        job = self.create()['job']
        with ThreadPoolExecutor(max_workers=8) as ex:
            claims = list(ex.map(lambda _: self.call('claim_job', {}, 'a'), range(8)))
        active = [c for c in claims if c['job']]
        self.assertEqual(len(active), 1)
        self.app.run('UPDATE jobs SET lease_until=0 WHERE id=?', (job['id'],))
        new = self.call('claim_job', {}, 'a')
        args = {'job_id': job['id'], 'claim_token': active[0]['claim_token'], 'status': 'succeeded', 'result': 'old'}
        with self.assertRaises(Error):
            self.call('report_job', args, 'a')
        args.update(claim_token=new['claim_token'], result='actual answer')
        self.call('report_job', args, 'a')
        self.assertEqual(self.call('report_job', args, 'a')['status'], 'succeeded')
        args['result'] = 'different'
        with self.assertRaises(Error):
            self.call('report_job', args, 'a')

    def test_waiting_job_takes_late_final_result(self):
        jid = self.create()['job']['id']
        claim = self.call('claim_job', {}, 'a')
        waiting = {'job_id': jid, 'claim_token': claim['claim_token'], 'status': 'waiting', 'result': 'Delegated to alex; link to follow'}
        self.assertIsNone(self.call('report_job', waiting, 'a')['lease_until'])
        self.assertEqual(self.call('report_job', waiting, 'a')['status'], 'waiting')
        for name, args in [('report_job', dict(waiting, status='succeeded', result='early')), ('renew_job', {'job_id': jid, 'claim_token': claim['claim_token']})]:
            with self.assertRaises(Error):
                self.call(name, args, 'a')
        self.assertIsNone(self.call('claim_job', {}, 'a')['job'])
        self.assertIsNone(self.call('claim_job', {'job_id': jid}, 'b')['job'])
        started = time.monotonic()
        polled = self.call('get_job', {'job_id': jid, 'wait_seconds': 5})
        self.assertEqual((polled['status'], polled['result']), ('waiting', 'Delegated to alex; link to follow'))
        self.assertLess(time.monotonic() - started, 1)
        self.app.wake_enabled = True
        self.app.bots['a'].update(webhook_url='https://api2.cursor.sh/automations/webhook/example', webhook_key_env='BOT_A')
        with patch('bridge.build_opener') as opener:
            self.assertEqual(self.call('wake_job', {'job_id': jid})['wake_count'], 0)
            opener.assert_not_called()
        with ThreadPoolExecutor(max_workers=4) as ex:
            claims = [c for c in ex.map(lambda _: self.call('claim_job', {'job_id': jid}, 'a'), range(4)) if c['job']]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['job']['status'], 'running')
        with self.assertRaises(Error):
            self.call('report_job', waiting, 'a')
        final = self.call('report_job', {'job_id': jid, 'claim_token': claims[0]['claim_token'], 'status': 'succeeded', 'result': 'Doc link'}, 'a')
        self.assertEqual((final['status'], final['result']), ('succeeded', 'Doc link'))

    def test_waiting_discovery_binds_only_after_final(self):
        with patch('bridge.secrets.choice', return_value='a'):
            jid = self.call('discover_hub', {'conversation_id': 'chat', 'request_id': 'd'})['job']['id']
        claim = self.call('claim_job', {}, 'a')
        args = {'job_id': jid, 'claim_token': claim['claim_token'], 'status': 'waiting', 'result': 'Asking chief of staff'}
        with self.assertRaises(Error):
            self.call('report_job', dict(args, hub_bot_id='a'), 'a')
        self.call('report_job', args, 'a')
        with self.assertRaises(Error):
            self.call('bind_hub', {'conversation_id': 'chat', 'discovery_job_id': jid})
        claim = self.call('claim_job', {'job_id': jid}, 'a')
        self.call('report_job', dict(args, claim_token=claim['claim_token'], status='succeeded', result='a is hub', hub_bot_id='a'), 'a')
        self.assertEqual(self.call('bind_hub', {'conversation_id': 'chat', 'discovery_job_id': jid})['hub_bot_id'], 'a')

    def test_scope_and_claim_token_separation(self):
        jid = self.create()['job']['id']
        self.assertIsNone(self.call('claim_job', {}, 'b')['job'])
        for name, args, role in [('get_job', {'job_id': jid}, 'b'), ('set_hub', {'conversation_id': 'chat', 'bot_id': 'b'}, 'a'), ('claim_job', {}, 'owner')]:
            with self.assertRaises(Error):
                self.call(name, args, role)
        claim = self.call('claim_job', {}, 'a')
        self.assertNotIn('claim_hash', self.call('get_job', {'job_id': jid}))
        self.assertNotIn('claim_token', self.call('get_job', {'job_id': jid}))
        with self.assertRaises(Error):
            self.call('report_job', {'job_id': jid, 'claim_token': claim['claim_token'], 'status': 'succeeded', 'result': 'spoof'}, 'b')

    def test_invalid_discovery_cannot_bind(self):
        job = self.call('discover_hub', {'conversation_id': 'chat', 'request_id': 'discovery'})['job']
        claim = self.call('claim_job', {}, job['bot_id'])
        with self.assertRaises(Error):
            self.call('report_job', {'job_id': job['id'], 'claim_token': claim['claim_token'], 'status': 'succeeded', 'result': 'maybe', 'hub_bot_id': 'missing'}, job['bot_id'])
        with self.assertRaises(Error):
            self.call('bind_hub', {'conversation_id': 'chat', 'discovery_job_id': job['id']})

    def test_lease_renew_and_long_poll(self):
        jid = self.create()['job']['id']
        claim = self.call('claim_job', {}, 'a')
        args = {'job_id': jid, 'claim_token': claim['claim_token']}
        self.assertGreaterEqual(self.call('renew_job', args, 'a')['lease_until'], claim['job']['lease_until'])
        with ThreadPoolExecutor() as ex:
            fut = ex.submit(self.call, 'get_job', {'job_id': jid, 'wait_seconds': 2})
            self.call('report_job', dict(args, status='failed', result='cannot complete'), 'a')
            self.assertEqual(fut.result()['status'], 'failed')

    def test_persistence(self):
        jid = self.create()['job']['id']
        self.call('set_hub', {'conversation_id': 'chat', 'bot_id': 'b'})
        other = Bridge(self.config, self.dbpath)
        try:
            self.assertEqual(other.call('owner', 'get_job', {'job_id': jid})['status'], 'queued')
            self.assertEqual(other.call('owner', 'get_conversation', {'conversation_id': 'chat'})['hub_bot_id'], 'b')
        finally:
            other.db.close()

    def test_wake_acceptance_is_not_completion_and_no_secret_payload(self):
        self.app.wake_enabled = True
        self.app.bots['a'].update(webhook_url='https://api2.cursor.sh/automations/webhook/example', webhook_key_env='BOT_A')
        with patch('bridge.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.status = 200
            job = self.create()['job']
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(req.data), {'event': 'gb2gpt_jobs_available'})
            self.assertEqual(job['wake_status'], 'accepted')
            self.assertEqual(job['status'], 'queued')
            self.create()
            self.assertEqual(opener.return_value.open.call_count, 1)
            with self.assertRaises(Error):
                self.call('wake_job', {'job_id': job['id']})

    def test_wake_timeout_is_unknown_and_still_queued(self):
        self.app.wake_enabled = True
        self.app.bots['a'].update(webhook_url='https://api2.cursor.sh/automations/webhook/example', webhook_key_env='BOT_A')
        with patch('bridge.build_opener') as opener:
            opener.return_value.open.side_effect = TimeoutError()
            job = self.create()['job']
        self.assertEqual(job['wake_status'], 'unknown')
        self.assertEqual(job['status'], 'queued')

    def test_transport_and_auth(self):
        status, headers, _ = self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
        self.assertEqual(status, 401)
        self.assertIn('resource_metadata', headers['WWW-Authenticate'])
        token = os.environ['BRIDGE_TOKEN']
        status, _, data = self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}, token)
        self.assertEqual(data['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(self.http('/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, token)[0], 202)
        self.assertEqual(self.http('/mcp', token=token)[0], 405)
        self.assertEqual(self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, token, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, token, {'MCP-Protocol-Version': 'wrong'})[0], 400)
        _, _, data = self.http('/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, os.environ['BOT_A'])
        self.assertNotIn('create_job', [t['name'] for t in data['result']['tools']])
        _, _, data = self.http('/api/create_job', {'conversation_id': 'chat', 'request_id': 'http', 'message': 'hello', 'bot_id': 'a'}, token)
        claim = self.http('/api/claim_job', {}, os.environ['BOT_A'])[2]
        _, _, report = self.http('/api/report_job', {'job_id': data['job']['id'], 'claim_token': claim['claim_token'], 'status': 'succeeded', 'result': 'hello from bot'}, os.environ['BOT_A'])
        self.assertEqual(report['result'], 'hello from bot')

    def oauth_code(self):
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        data = {'client_id': 'gb2gpt', 'redirect_uri': self.config['oauth_redirect_uris'][0], 'resource': self.app.resource, 'response_type': 'code', 'code_challenge_method': 'S256', 'code_challenge': challenge, 'state': 'test-state', 'scope': 'bridge'}
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())
        status, headers, body = self.http('/authorize?' + urlencode(data), opener=opener)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Referrer-Policy'], 'strict-origin')
        form = re.search(r'name="form" value="([^"]+)"', body).group(1)
        status, headers, _ = self.http('/authorize', {'form': form, 'password': os.environ['BRIDGE_TOKEN']}, form=True, opener=opener, headers={'Origin': self.base})
        self.assertEqual(status, 302)
        callback = parse_qs(urlsplit(headers['Location']).query)
        self.assertEqual(callback['iss'][0], self.base)
        self.assertEqual(callback['state'][0], 'test-state')
        return {'client_id': 'gb2gpt', 'client_secret': os.environ['OAUTH_CLIENT_SECRET'], 'grant_type': 'authorization_code', 'resource': self.app.resource, 'code': callback['code'][0], 'code_verifier': verifier, 'redirect_uri': data['redirect_uri']}

    def test_oauth_pkce_replay_refresh_and_audience(self):
        args = self.oauth_code()
        bad = dict(args, code_verifier='x' * 43)
        self.assertEqual(self.http('/token', bad, form=True)[0], 400)
        status, _, tokens = self.http('/token', args, form=True)
        self.assertEqual(status, 200)
        self.assertEqual(self.app.principal(tokens['access_token']), 'owner')
        self.assertEqual(self.http('/token', args, form=True)[0], 400)
        with self.assertRaises(Error):
            self.app.principal(tokens['refresh_token'])
        refresh = {'client_id': 'gb2gpt', 'client_secret': os.environ['OAUTH_CLIENT_SECRET'], 'grant_type': 'refresh_token', 'resource': self.app.resource, 'refresh_token': tokens['refresh_token']}
        self.assertEqual(self.http('/token', dict(refresh, resource='https://other.example/mcp'), form=True)[0], 400)
        self.assertEqual(self.http('/token', refresh, form=True)[0], 200)
        self.assertEqual(self.http('/token', refresh, form=True)[0], 400)
        self.app.run('UPDATE oauth_tokens SET expires=0 WHERE hash=?', (digest(tokens['access_token']),))
        with self.assertRaises(Error):
            self.app.principal(tokens['access_token'])

    def test_oauth_redirect_and_csrf_rejection(self):
        self.assertEqual(self.http('/authorize?' + urlencode({'client_id': 'gb2gpt', 'redirect_uri': 'https://evil.example'}))[0], 400)
        self.assertEqual(self.http('/authorize', {'form': 'fake', 'password': os.environ['BRIDGE_TOKEN']}, form=True)[0], 403)
        for origin in ('null', 'https://evil.example'):
            self.assertEqual(self.http('/authorize', {'form': 'fake', 'password': os.environ['BRIDGE_TOKEN']}, form=True, headers={'Origin': origin})[0], 403)

    def test_input_validation_and_webhook_ssrf(self):
        for extra in ({'dry_run': 'false'}, {'extra': 'field'}):
            with self.assertRaises(Error):
                self.create(**extra)
        with self.assertRaises(Error):
            self.call('create_job', {'conversation_id': 'chat', 'request_id': 'nohub', 'message': 'hello'})
        self.config['bots'][0]['webhook_url'] = 'https://127.0.0.1/private'
        with self.assertRaises(ValueError):
            Bridge(self.config, self.dbpath)


if __name__ == '__main__':
    unittest.main()
