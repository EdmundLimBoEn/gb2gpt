#!/usr/bin/env python3
"""REST fallback for a bot computer whose MCP UI cannot store a Bearer header.
Set GB2GPT_URL and GB2GPT_BOT_TOKEN in the bot's secure process environment.
Tool arguments are JSON on stdin; output is JSON. Never pass tokens on argv.
"""
import json
import os
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

TOOLS = {'list_bots', 'claim_job', 'renew_job', 'report_job', 'get_job'}
if len(sys.argv) != 2 or sys.argv[1] not in TOOLS:
    raise SystemExit('Usage: bot_client.py list_bots|claim_job|renew_job|report_job|get_job < args.json')
url = os.environ.get('GB2GPT_URL', '').rstrip('/')
u = urlsplit(url)
if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
    raise SystemExit('GB2GPT_URL must be the public HTTPS origin')
token = os.environ.get('GB2GPT_BOT_TOKEN', '')
if not token:
    raise SystemExit('Set GB2GPT_BOT_TOKEN in the secure environment, never chat')
args = json.load(sys.stdin)
req = Request(url + '/api/' + sys.argv[1], data=json.dumps(args).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token, 'User-Agent': 'gb2gpt-bot/1.0.0'})
# Same-origin fixed endpoint; do not forward credentials through redirects.
from urllib.request import HTTPRedirectHandler, build_opener
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
try:
    with build_opener(NoRedirect()).open(req, timeout=25) as response:
        print(response.read().decode())
except HTTPError as e:
    print(e.read().decode(), file=sys.stderr)
    raise SystemExit(1)
except (URLError, TimeoutError):
    raise SystemExit('Bridge unavailable; retry with the same job/request identifiers')
