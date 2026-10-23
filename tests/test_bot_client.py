import io
import json
import os
from pathlib import Path
import runpy
import secrets
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import Bridge, Handler


class BotClientTest(unittest.TestCase):
    def test_reports_large_unicode_results(self):
        env = {key: secrets.token_urlsafe(32) for key in
               ('BRIDGE_TOKEN', 'OAUTH_CLIENT_SECRET', 'GB2GPT_BOT_TOKEN')}
        env.update(GB2GPT_URL='https://bridge.example', WAKE_ENABLED='false')
        config = {'fleet_name': 'Test', 'public_url': env['GB2GPT_URL'],
                  'oauth_redirect_uris': ['https://example.com/callback'],
                  'bots': [{'id': 'a', 'name': 'A', 'token_env': 'GB2GPT_BOT_TOKEN'}]}
        with patch.dict(os.environ, env):
            app = Bridge(config, ':memory:')
            self.addCleanup(app.db.close)

            def receive(req, timeout):
                self.assertEqual(req.full_url, env['GB2GPT_URL'] + '/api/report_job')
                # Pass the actual adapter bytes through the server's body limit,
                # UTF-8 decoder and schema validation without opening a socket.
                handler = Handler.__new__(Handler)
                handler.headers = {'Content-Length': str(len(req.data)),
                                   'Content-Type': req.get_header('Content-type')}
                handler.rfile = io.BytesIO(req.data)
                reported = app.call('a', 'report_job', handler.read())
                return io.BytesIO(json.dumps(reported).encode('utf-8'))

            for count in (11000, 20000):
                with self.subTest(characters=count):
                    job = app.call('owner', 'create_job', {
                        'conversation_id': 'chat', 'request_id': f'unicode-{count}',
                        'message': 'hello', 'bot_id': 'a'})['job']
                    claim = app.call('a', 'claim_job', {})
                    result = '\U0001f600' * count
                    args = {'job_id': job['id'], 'claim_token': claim['claim_token'],
                            'status': 'succeeded', 'result': result}
                    output = io.StringIO()
                    with patch.object(sys, 'argv', ['bot_client.py', 'report_job']), \
                         patch.object(sys, 'stdin', io.StringIO(json.dumps(args))), \
                         patch.object(sys, 'stdout', output), \
                         patch('urllib.request.build_opener') as opener:
                        opener.return_value.open.side_effect = receive
                        runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts' / 'bot_client.py'), run_name='__main__')
                    self.assertEqual(json.loads(output.getvalue())['status'], 'succeeded')
                    saved = app.call('owner', 'get_job', {'job_id': job['id']})
                    self.assertEqual((saved['status'], saved['result']), ('succeeded', result))
