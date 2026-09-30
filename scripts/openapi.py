#!/usr/bin/env python3
"""Generate optional Custom GPT Action schema without loading fleet or secrets."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import tool_specs
if len(sys.argv) != 2:
    raise SystemExit('Usage: openapi.py https://YOUR-BRIDGE-HOST')
u = urlsplit(sys.argv[1])
if u.scheme != 'https' or not u.hostname or u.path not in ('', '/') or u.query or u.fragment or u.username:
    raise SystemExit('Use an HTTPS origin without credentials')
paths = {}
for t in tool_specs('owner'):
    paths['/api/' + t['name']] = {'post': {'operationId': t['name'], 'description': t['description'], 'x-openai-isConsequential': not t['annotations']['readOnlyHint'], 'requestBody': {'required': True, 'content': {'application/json': {'schema': t['inputSchema']}}}, 'responses': {'200': {'description': 'Job or tool result; queued/running/waiting is not completion', 'content': {'application/json': {'schema': {'type': 'object', 'additionalProperties': True}}}}}}}
print(json.dumps({'openapi': '3.1.0', 'info': {'title': 'gb2gpt', 'version': '1.0.0'}, 'servers': [{'url': sys.argv[1].rstrip('/')}], 'security': [{'bearerAuth': []}], 'components': {'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}}, 'paths': paths}, indent=2))
