# gb2gpt v1

Chat with your **Cursor Grok Bot fleet from an ordinary ChatGPT conversation** using a personal Developer-mode MCP connection. One small Python server stores jobs, routes through a discovered hub or dedicated relay, optionally wakes bots through official routine webhooks, and returns their reported replies.

**Zero Python package dependencies. One process, one SQLite file, optional one-container deployment.** No OpenAI/xAI API key, no extra paid SaaS required by the bridge. This is an independent composition, not an official integration and not grok.com.

**What you need before starting.** gb2gpt is only useful if you already use [Cursor Grok Bots](https://cursor.com/help/grok-bot): cloud agents with their own secrets, bot-to-bot messaging, and *routines* (saved instructions a bot runs manually, on a schedule, or when its webhook is called). The bridge does not run a model itself; your bots do the work and report back. Without a Grok Bot fleet there is nothing for ChatGPT to talk to.

Two boundaries matter: the server cannot write native ChatGPT memory, and it cannot push a late reply into an idle chat. The first-conversation prompt requests a memory save and asks you to verify it; pending replies are polled, and delegated work that finishes later lands on the same job. See [ChatGPT setup](CHATGPT_SETUP.md) and the [architecture/threat model](docs/DESIGN.md).

## Recommended: your own relay bot

Create a dedicated Grok Bot named `gb2gpt`. ChatGPT sends jobs to that bot, which contacts your real chief of staff through bot-to-bot messaging and reports the reply. It speaks as a relay, never as you. This needs only one scoped worker credential; your other bots do not need bridge credentials. Follow the [reproducible setup guide](docs/SELF_HOSTING.md), use [fleet.relay.example.json](fleet.relay.example.json), and supply both [worker](prompts/bot.md) and [relay](prompts/relay.md) instructions. Direct multi-worker fleets remain supported below.

Terms used in these docs:

| Term | Meaning |
| --- | --- |
| Hub | The one bot a ChatGPT conversation sends messages to by default. Found once per chat by `discover_hub`, then stored by `bind_hub`. |
| Chief of staff | Whichever of your existing bots coordinates the others. With the relay setup it sits behind the relay and needs no bridge credential. |
| Relay | A dedicated Grok Bot named `gb2gpt` that is the hub and passes messages to your chief of staff. |
| Worker | Any bot configured in `fleet.json`. It claims jobs from the bridge and reports results using its own token. |
| Wake / doorbell | An optional POST to the bot's official routine webhook so it starts a run immediately. It carries no job data. |
| Ritual | The first message you paste into a ChatGPT conversation (see [CHATGPT_SETUP.md](CHATGPT_SETUP.md)). It sets up discovery and routing for that chat. |

Each person runs a separate single-owner instance with their own accounts, hostname, bot, and secrets. This is self-hostable software, not a shared multi-user service.

## Quickstart

Requires Python 3.11+ (tested on 3.12, 3.13 and 3.14; standard library only, nothing to `pip install`), or Docker. To use real bots you also need a paid ChatGPT account whose workspace allows Developer mode, a Cursor account with Grok Bots/routines available, and an HTTPS address reachable by both. [ChatGPT eligibility](https://developers.openai.com/api/docs/guides/developer-mode). No existing account credentials are included.

```bash
git clone https://github.com/EdmundLimBoEn/gb2gpt.git
cd gb2gpt
cp fleet.relay.example.json fleet.json   # recommended relay setup; or copy fleet.example.json and edit it
python3 scripts/init.py
python3 scripts/smoke.py
python3 bridge.py
```

**Choose your fleet before running `init.py`.** It generates one random token for `BRIDGE_TOKEN`, one for `OAUTH_CLIENT_SECRET`, and one for each bot's `token_env` in `fleet.json` *as it exists at that moment*. It writes them to `.env` with mode 0600, never prints them, and refuses to overwrite an existing `.env`. If `fleet.json` is missing, it copies the two-bot Alpha/Beta `fleet.example.json`, which is only an example. If you add a bot later, add its `BOT_…_TOKEN=` line to `.env` yourself, e.g. with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. `init.py` never creates `*_WEBHOOK_KEY` values; those come from Cursor (see [Optional wake](#optional-wake-for-normal-interactive-use)).

Run `bridge.py` from the checkout directory: `fleet.json`, `.env` and `data/bridge.sqlite3` are resolved relative to the current directory (override with `--config`, `--env-file`, `--db`, `--host`, `--port`). Restart after configuration changes. `.env` is loaded as literal `KEY=value`; existing environment variables take precedence. Do not `source` untrusted env files.

Health: `http://127.0.0.1:8787/health`. Leave `public_url` as `http://127.0.0.1:8787` until you have HTTPS (next section). The smoke script starts an isolated temporary server, uses generated throwaway credentials, checks real HTTP health and MCP dry-run creation, then confirms the bot queue is empty. It never loads your fleet, wakes a bot, or modifies your ledger.

To use ChatGPT, finish the once-only steps below: HTTPS → bot workers → [ChatGPT connection and ritual](CHATGPT_SETUP.md). Wake starts disabled. Until a bot worker runs, real jobs remain queued.

## Reachable HTTPS

For ongoing use, put the bridge on an always-on host behind your existing HTTPS reverse proxy, with one stable hostname. Forward all paths to `127.0.0.1:8787`, preserve `Authorization` and `Origin`, allow 30-second requests, limit bodies to 128 KiB, and apply request/connection limits. Suppress request queries and sensitive headers/bodies in proxy logs. Do not add an interactive proxy login in front of MCP; the bridge handles OAuth. Set `fleet.json.public_url` to that HTTPS origin and restart. Keep the raw port on loopback/private networking.

`public_url` must be a bare origin: scheme and host (and port if non-standard), no path and no trailing path segments. It must be `https://` unless the host is `127.0.0.1` or `localhost`, so a LAN IP like `http://192.168.1.5:8787` is rejected. The bridge compares browser `Origin` headers against it exactly, and OAuth tokens are bound to `<public_url>/mcp`, so it must be the exact hostname ChatGPT uses.

For a quick local trial, install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) and, in another terminal, run:

```bash
cloudflared tunnel --url http://127.0.0.1:8787
```

Put its generated HTTPS origin in `fleet.json.public_url`, restart the bridge, and keep both processes running. Quick tunnels use temporary URLs and are for testing; use a stable deployment before treating setup as permanent. This server returns JSON over Streamable HTTP and does not require SSE. A tunnel/provider can observe transport data at its TLS boundary. No tunnel is installed or started by the bridge.

Both ChatGPT and your bots run in the cloud, so the bridge must be reachable from the public internet over HTTPS. A tunnel that only ChatGPT can reach is not enough.

## Portable fleet configuration

`fleet.json` is ignored by Git. It contains identity and deployment configuration; credentials are referenced by env name. `.env.example` contains **only credentials and the wake flag**.

```json
{
  "fleet_name": "My bot fleet",
  "public_url": "https://bridge.example.net",
  "oauth_redirect_uris": [
    "https://chatgpt.com/connector_platform_oauth_redirect"
  ],
  "bots": [
    {
      "id": "alpha",
      "name": "Alpha",
      "token_env": "BOT_ALPHA_TOKEN"
    },
    {
      "id": "beta",
      "name": "Beta",
      "token_env": "BOT_BETA_TOKEN",
      "webhook_url": "https://api2.cursor.sh/automations/webhook/REPLACE_WITH_ROUTINE_ID",
      "webhook_key_env": "BOT_BETA_WEBHOOK_KEY"
    }
  ]
}
```

Bot IDs are your stable aliases (1–100 letters, numbers, `_` or `-`), not assumed Cursor internal IDs. Give bots this exact alias-to-name mapping. Each bot has a different token, at least 32 characters, stored only in `.env` and that bot's secure credential store. There is **no configured hub field**: first discovery asks a random bot. Every configured bot must be able to process jobs and identify the configured routing hub (which may be a dedicated relay); configure only bots that are ready.

`BRIDGE_TOKEN` is the owner credential for the OAuth login/local REST/optional Actions. `OAUTH_CLIENT_SECRET` is distinct and belongs in ChatGPT's OAuth client-secret setting. Neither belongs on a bot. `BOT_*_TOKEN` authenticates a worker; `BOT_*_WEBHOOK_KEY` is the different, Cursor-issued secret used only by the server to wake that bot. Changing the public origin requires reconnecting OAuth clients. All secrets must be at least 32 printable ASCII characters with no spaces and must all differ from each other; the bridge refuses to start otherwise.

`.env` format is strict: one `KEY=value` per line, `#` comments allowed, no quotes, no `export`, no spaces around `=`. Anything else stops startup with `Invalid .env line`.

## Configure each Grok Bot once

1. Put the bridge's corresponding bot token into that bot's **Secrets → Add secret**, named `GB2GPT_BOT_TOKEN`; use Cursor's masked secure form. Never paste it in a conversation, routine instruction, or shell command. Set the non-secret `GB2GPT_URL` to your HTTPS bridge origin in its runtime configuration. [Cursor secret storage](https://cursor.com/help/grok-bot/secrets).
2. Connect that bot to the bridge with **one** of the following methods. Verify `list_bots` and `claim_job` before relying on it. A working adapter, not just a webhook, is required for replies.
3. Add [prompts/bot.md](prompts/bot.md) as trusted worker/routine instructions and tell each bot the actual fleet aliases and who coordinates it. Do not instruct it to guess the hub.
4. Run the worker manually once. For routine use, configure either the optional webhook below or an owner-chosen schedule. No bot polling daemon is shipped; the Grok Bot agent processes the jobs during its routine.

**MCP worker:** where your Grok Bot connector supports securely configured request headers, use Streamable HTTP `https://YOUR-BRIDGE-HOST/mcp` and a Bearer header bound to that bot's stored token. The bridge exposes only `list_bots`, `get_job`, `claim_job`, `renew_job`, `report_job` to bot credentials. The exact secure header UI is client-dependent; this project does not assume it exists in every Cursor build. Do not use owner OAuth for workers. Cursor plugins can be account-wide; avoid sharing every bot credential with every bot. Use the following per-bot adapter if needed. [Cursor plugin sharing](https://cursor.com/help/grok-bot/connect-plugins).

**REST worker fallback:** copy `scripts/bot_client.py` onto each bot's cloud computer; it needs only Python 3.9+ and no packages. Have its trusted routine invoke it with the bot's secret securely injected as `GB2GPT_BOT_TOKEN` and the non-secret `GB2GPT_URL`. Ask the bot to use the stored secret without printing it. If the host cannot securely inject a credential into the worker or connector, complete that host setup before proceeding; putting it in chat is not a workaround. Map each tool in `prompts/bot.md` to this command:

```bash
python3 /path/to/bot_client.py claim_job <<'JSON'
{}
JSON
```

The command prints the claimed job and its temporary claim receipt, **not the bot's credential**. The routine performs the work, then passes the job ID, claim receipt, status, and answer as JSON on stdin to `bot_client.py report_job`. For discovery, also supply `hub_bot_id`. Use `bot_client.py renew_job` before the lease expires. All five worker tools use the same JSON arguments as MCP. Keep job receipts in worker execution context; they are not user setup secrets and should not be copied to the ChatGPT conversation.

A secret-free prompt for creating the routine after the adapter is working:

```text
Set up a gb2gpt worker routine using the trusted worker instructions I supplied.
On each run, ignore the webhook body, claim my own jobs from the bridge using my
securely stored credential, process at most ten jobs, and report each result
back to the bridge. Stop when the queue is empty. Add a webhook trigger.
```

### Optional wake for normal interactive use

Open each worker routine's **Webhook** section on Grok Bot desktop. Copy its official **POST to** URL into that bot's `webhook_url` in local `fleet.json` (it must look like `https://api2.cursor.sh/automations/webhook/<id>`), add `"webhook_key_env": "BOT_<NAME>_WEBHOOK_KEY"` next to it, and copy its **key** into that variable in local `.env`. With wake enabled, a bot that has `webhook_url` but no `webhook_key_env` stops startup with `Configuration error: missing field 'webhook_key_env'`. Add the env variable if the initializer did not generate it. Keep keys out of chat and source control. Ensure the routine is **Active**. Set **`WAKE_ENABLED=true`** and restart only when you want submissions to start bot runs and consume usage. [Official routine setup](https://cursor.com/help/grok-bot/routines).

Wake defaults to **false**, including in `.env.example` and the initializer. With wake disabled, use a manual worker run or a schedule you explicitly configured. There is no hidden background wake. Only the documented `api2.cursor.sh` webhook shape is allowed; changed upstream URL formats need an explicit code review/update.

HTTP **200 means accepted**, never finished. A reply exists only after the bot calls `report_job`. Timeout is `unknown` because the remote service may have accepted it. Inspect the job/routine before requesting another wake; automatic retries do not spend extra usage.

## Docker alternative

After generating/editing `.env` and `fleet.json`. The container runs as uid 10001 and reads `fleet.json` through a bind mount, so it must be readable by that user. `fleet.json` holds no secrets, so `chmod 644 fleet.json` is fine. A 0600 file owned by you fails with `Configuration error: [Errno 13] Permission denied`. On Linux, set `public_url` to your HTTPS origin first; the container listens on `0.0.0.0` inside Docker but is published only on host loopback here.

```bash
docker build -t gb2gpt:v1 .
docker volume create gb2gpt-data
docker run -d --name gb2gpt --restart unless-stopped \
  --env-file .env \
  -p 127.0.0.1:8787:8787 \
  --mount type=bind,src="$(pwd)/fleet.json",dst=/app/fleet.json,readonly \
  --mount type=volume,src=gb2gpt-data,dst=/data \
  gb2gpt:v1
```

One unprivileged container; named volume retains the SQLite ledger and OAuth state. `.env` is excluded from the image and supplied at runtime. A TLS proxy/tunnel still supplies HTTPS. To change env values, recreate the container with the same named volume; restarting alone does not reload Docker's env file. Do not run multiple bridge replicas against this volume.

## Tools and REST

`POST /mcp` implements initialization, ping, tools/list and tools/call, with stateless JSON responses. Notifications receive 202. Authenticated GET/DELETE returns 405 because no SSE subscription/session is maintained. No custom `search`/`fetch` tools are needed. No RPC can save native ChatGPT memory.

Every tool is also `POST /api/TOOL_NAME`, JSON arguments, `Authorization: Bearer …`, same permissions. Owner and worker scopes are distinct. Argument schemas and descriptions are available through `tools/list`.

| Identity | Tools |
| --- | --- |
| Owner / ChatGPT OAuth | `list_bots`, `discover_hub`, `bind_hub`, `set_hub`, `get_conversation`, `create_job`, `get_job`, `wake_job` |
| Per-bot credential | `list_bots`, `claim_job`, `renew_job`, `report_job`, `get_job` (own jobs only) |

Job submissions require `conversation_id` and `request_id`; retries must preserve both and all content. Use a fresh request ID for a genuinely new request. Default routing uses this chat's hub. `bot_id` overrides only that job. `dry_run=true` never persists or wakes. `get_job` accepts `wait_seconds` from 0 to 20. Claim leases last 15 minutes. Jobs carry only the message/context ChatGPT submits, not an automatically synchronized transcript.

For the optional Custom GPT Action schema, see [CHATGPT_SETUP.md](CHATGPT_SETUP.md#optional-custom-gpt-action-path). Ordinary MCP chats remain the default.

## Operation and validation

```bash
python3 -m unittest discover -s tests -v
python3 scripts/smoke.py
```

Tests cover the queue/auth/routing failure boundaries and use a mocked wake transport. No tests contact a real Cursor webhook. Optional independent protocol check (kept outside runtime dependencies):

```bash
python3 -m venv /tmp/gb2gpt-sdk-check
/tmp/gb2gpt-sdk-check/bin/pip install 'mcp==2.2.0'
/tmp/gb2gpt-sdk-check/bin/python scripts/check_mcp_sdk.py
```

Keep the data volume private: job bodies/results are stored in plaintext. Back up after stopping the bridge, or use SQLite's online backup API. No automatic retention is configured. The server intentionally does not log request paths, tokens, headers, or bodies; check your reverse proxy's logging too.

ChatGPT's OAuth access tokens last 1 hour and are refreshed automatically; refresh tokens last 90 days and rotate on each use. If ChatGPT goes unused for more than 90 days, reconnect the app.

To revoke linked ChatGPT access, stop the bridge, delete OAuth rows (jobs are kept):

```bash
python3 -c "import sqlite3; db=sqlite3.connect('data/bridge.sqlite3'); db.executescript('DELETE FROM oauth_tokens; DELETE FROM oauth_codes; DELETE FROM oauth_forms;'); db.close()"
```

then rotate `BRIDGE_TOKEN`/`OAUTH_CLIENT_SECRET` locally, restart and reconnect. Rotating the owner token alone does not revoke already-issued OAuth access tokens. Rotate a bot token in `.env` and its secure bot store together, then restart. Never delete the jobs database merely to rotate credentials.

Security note: the bridge login page has no lockout, so `BRIDGE_TOKEN` is the only barrier against guessing. It is 256 bits of randomness when generated by `init.py`; keep it that way and keep the rate-limited proxy in front for any remote deployment.

## First-run troubleshooting

| Message | Fix |
| --- | --- |
| `Configuration error: [Errno 2] No such file or directory: 'fleet.json'` | Run from the checkout directory, and create `fleet.json` (see Quickstart). |
| `BRIDGE_TOKEN must contain at least 32 non-whitespace ASCII characters` | `.env` is missing or not in the current directory. Run `python3 scripts/init.py`, or pass `--env-file`. |
| `BOT_…_TOKEN must contain at least 32 …` | A bot in `fleet.json` has no token in `.env`. Add one (see Quickstart). |
| `Invalid .env line; use KEY=value without shell syntax` | Remove quotes, `export`, or spaces from `.env`. |
| `public_url requires HTTPS except on loopback` / `public_url must be a bare origin` | See [Reachable HTTPS](#reachable-https). |
| `missing field 'webhook_key_env'` | See [Optional wake](#optional-wake-for-normal-interactive-use). |
| `[Errno 13] Permission denied` in Docker | `chmod 644 fleet.json` (see [Docker alternative](#docker-alternative)). |
| `[Errno 48]` / `[Errno 98] Address already in use` | Another process has port 8787. Stop it or use `--port`. |
| ChatGPT can't connect or log in | See [If it stalls](CHATGPT_SETUP.md#if-it-stalls) and the [layer-by-layer diagnosis](docs/SELF_HOSTING.md#diagnose-one-layer-at-a-time). |

`init.py` and `smoke.py` take no arguments. `openapi.py` takes one HTTPS origin, and `bot_client.py` takes one tool name with JSON on stdin.

Limits: one owner/fleet per instance; no public service hardening or multi-user tenancy; no automatic notification into idle ChatGPT; no guarantee that a model always calls tools; no programmatic native-memory write; no exactly-once external effects after worker failure. Put the stdlib HTTP process behind a rate-limited TLS proxy for remote use. Real ChatGPT/Cursor account linking and native-memory verification require the operator's live acceptance test described in [docs/DESIGN.md](docs/DESIGN.md#verification-and-live-acceptance).

## License

[CC BY-NC 4.0](LICENSE). Not affiliated with OpenAI, Cursor, or xAI.
