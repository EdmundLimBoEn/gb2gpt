#!/usr/bin/env python3
"""Optional wire check using official MCP Python SDK 2.2.0; no real wake."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import Bridge, Server


@asynccontextmanager
async def client(url, token):
    async with create_mcp_http_client(headers={'Authorization': 'Bearer ' + token}) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session


async def check(url, owner, worker):
    async with client(url, owner) as c:
        tools = await c.list_tools()
        assert 'discover_hub' in [t.name for t in tools.tools]
        result = await c.call_tool('discover_hub', {'conversation_id': 'sdk-chat', 'request_id': 'discover'})
        assert not result.is_error
        jid = result.structured_content['job']['id']
    async with client(url, worker) as c:
        tools = await c.list_tools()
        assert 'create_job' not in [t.name for t in tools.tools]
        result = await c.call_tool('claim_job', {})
        claim = result.structured_content
        assert claim['job']['id'] == jid
        result = await c.call_tool('report_job', {'job_id': jid, 'claim_token': claim['claim_token'], 'status': 'succeeded', 'result': 'Worker is the hub', 'hub_bot_id': 'worker'})
        assert not result.is_error
    async with client(url, owner) as c:
        result = await c.call_tool('get_job', {'job_id': jid})
        assert result.structured_content['status'] == 'succeeded'
        result = await c.call_tool('bind_hub', {'conversation_id': 'sdk-chat', 'discovery_job_id': jid})
        assert result.structured_content['hub_bot_id'] == 'worker'
        assert result.structured_content['chatgpt_memory_saved'] is False
        result = await c.call_tool('create_job', {'conversation_id': 'sdk-chat', 'request_id': 'dry', 'message': 'hi', 'dry_run': True})
        assert result.structured_content['persisted'] is False
    print('PASS official MCP SDK: initialize, tools/list, random discovery, claim/report, polling, hub binding, dry run')


if __name__ == '__main__':
    os.environ.update(BRIDGE_TOKEN=secrets.token_urlsafe(32), OAUTH_CLIENT_SECRET=secrets.token_urlsafe(32), SDK_BOT_TOKEN=secrets.token_urlsafe(32), WAKE_ENABLED='false')
    with tempfile.TemporaryDirectory() as tmp:
        app = Bridge({'fleet_name': 'SDK test', 'public_url': 'http://127.0.0.1:8787', 'oauth_redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect'], 'bots': [{'id': 'worker', 'name': 'Worker', 'token_env': 'SDK_BOT_TOKEN'}]}, str(Path(tmp, 'test.sqlite3')))
        server = Server(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(check(f'http://127.0.0.1:{server.server_port}/mcp', os.environ['BRIDGE_TOKEN'], os.environ['SDK_BOT_TOKEN']))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            app.db.close()
