# gb2gpt v1 — implementation and acceptance plan

A personal, single-owner bridge between ordinary ChatGPT conversations and an operator-configured Cursor Grok Bot fleet. Default: remote Streamable HTTP MCP with OAuth for ChatGPT, a SQLite job ledger, and authenticated bot workers. This is a composition of documented interfaces, not an official ChatGPT↔Grok Bot product. It does not use grok.com, xAI APIs, Discord, or a private `:1340` gateway.

## Architecture

```mermaid
sequenceDiagram
    participant U as User
    participant C as Normal ChatGPT chat
    participant M as gb2gpt MCP + SQLite
    participant R as Random configured bot
    participant H as Discovered hub bot
    U->>C: First-message ritual
    C->>M: discover_hub(chat ID, request ID)
    M->>M: secrets.choice(fleet); persist discovery job
    M-->>R: Optional official webhook doorbell
    R->>M: claim_job (own credential)
    M-->>R: Who is the chief-of-staff bot?
    R->>M: report_job(result, hub_bot_id)
    C->>M: get_job; bind_hub
    M-->>C: Validated hub identity + memory sentence
    C->>C: Request native ChatGPT memory save
    U->>C: Verify saved memory in Personalization
    U->>C: Subsequent message
    C->>M: create_job (omit bot_id)
    M->>M: Route to this chat's persisted hub
    M-->>H: Optional webhook doorbell
    H->>M: claim_job; report_job(answer)
    C->>M: get_job (bounded poll)
    M-->>C: Actual answer or still pending
    C-->>U: Attributed answer or pending job ID
```

One Python process and one SQLite file, persisted on a local disk/volume. No queue daemon, model API, frontend build, paid auth provider, scheduler, or fleet-specific runtime identity. HTTPS must be reachable by both remote clients: use an existing TLS reverse proxy or a tunnel. Run one instance per fleet/owner; not a multi-tenant service. Localhost alone cannot connect cloud clients.

ChatGPT speaks MCP at `/mcp`. It links via the server's single-owner OAuth authorization-code flow: static client `gb2gpt`, confidential client secret, S256 PKCE, exact callback allowlist, resource binding, one-use codes, expiring access tokens, rotating refresh tokens. The owner enters the bridge token only into the bridge's browser login form. Bot workers use a distinct Bearer credential per bot, through MCP if secure header configuration is available, or the included REST CLI on their cloud computer. Both paths call the same ledger code. OAuth issues owner access only; do not link bots using owner OAuth.

## Discovery, routing, and native memory

1. ChatGPT creates a non-secret conversation ID and invokes `discover_hub`. The server makes the random draw on ChatGPT's behalf, uniformly using `secrets.choice`. There is no configured/default chief, preferred first bot, or name-based inference.
2. The discovery is a real queued question to that randomly chosen bot. All configured bots must have a functioning worker and know the fleet's leadership. Offline/unknown/ambiguous discovery stays pending or fails; the bridge does not fabricate an answer. The user can explicitly retry another discovery or override.
3. The selected bot reports one existing `hub_bot_id`. Only a successful discovery from the same conversation can be used by `bind_hub`. A reply's arbitrary prose cannot select an unknown bot or run server code.
4. `bind_hub` persists the routing ID in SQLite and returns a non-secret `memory_text`. ChatGPT is instructed to request a native memory save and the user verifies it in Personalization. **The bridge has no API for writing or verifying ChatGPT memory.** It always reports `chatgpt_memory_saved=false`; this means the bridge did not save it, not that the user could not save it separately.
5. If that ChatGPT surface/workspace disables memory, the strict first-conversation native-memory requirement is unmet there. Save the returned identity sentence in a separate memory-enabled regular chat, then return; if memory is unavailable everywhere, only chat-local routing is available. Do not label the bridge database, context, or a file as ChatGPT memory. The fetched official docs establish account/workspace memory controls, not a guaranteed MCP memory-write capability.
6. Subsequent requests omit `bot_id` and use the durable hub for that conversation. A one-message override does not change it. Explicit `set_hub` changes the chat default. A new chat needs a new conversation ID and either user-authorized reuse of the remembered hub or discovery; MCP does not supply a reliable native ChatGPT thread ID here. Do not route unrelated conversations automatically.

Full helper instructions are in `prompts/chatgpt.md` and returned by MCP initialization. Model compliance and native memory remain host responsibilities; the server cannot intercept every ChatGPT utterance or force tool use.

## Async contract and recovery

`create_job` and `discover_hub` persist before optional wake. `(conversation_id, request_id)` makes retries idempotent; changed content with the same key conflicts. A dry run validates routing but neither inserts a job nor contacts a webhook. `queued → running → succeeded|failed` is the job state machine. An expired running claim can be reclaimed with a new receipt. Claims have 15-minute renewable leases. Old receipts and other bots cannot report. Repeated identical final reports succeed; conflicting rewrites fail.

Wake is a separate state: `disabled`, `not_configured`, `unknown`, `accepted`, or `rejected`. Only documented HTTP 200 means accepted. No webhook response body becomes a job result. Timeouts/crashes around wake leave uncertain delivery; no automatic network retry. Explicit wake retries are limited to five per job, at least 30 seconds apart. A trusted routine drains up to ten jobs per run; schedules can recover a missed wake if the owner chooses their usage cost. No hidden wake loop exists.

The bot must `report_job`; answering only in its own chat does not deliver to ChatGPT. `get_job` can wait up to 20 seconds; prompts recommend at most three 15-second polls in one ChatGPT turn. Later completion requires another check. This v1 cannot push an unsolicited message into an idle ChatGPT conversation. Outside effects are at-least-once: use job IDs for deduplication in external systems before repeating reclaimed work.

## Threat model

| Boundary / threat | v1 control and remaining responsibility |
| --- | --- |
| Arbitrary Internet clients read jobs or spend bot usage | All MCP/REST operations require scoped credentials. Only health and OAuth discovery/login are public. Owner controls one fleet; conversation IDs are grouping, not a tenant security boundary. |
| Credential theft | Generated secrets stay in ignored `.env`, worker secret stores, and OAuth settings. No secrets in URLs, tool arguments, prompts, image layers, or examples. OAuth and claim receipts stored hashed. Login form has CSRF cookie + one-use nonce, no third-party assets, no referrer, and no framing. HTTPS is mandatory remotely. |
| Prompt injection in webhook bodies or bot replies | Wake sends a fixed doorbell only. Routine ignores incoming body and fetches from authenticated ledger. Jobs/replies remain untrusted data, never evaluated by bridge; worker and ChatGPT instructions prohibit credential disclosure and authority escalation. Models still need ordinary safeguards for outside actions. |
| Worker impersonation / cross-bot reports | Credential determines bot identity; no caller-supplied role. Atomic claims, per-claim receipts, leases, and immutable final reports. Bots cannot set hubs, create jobs, or wake peers. A compromised bot can lie in a discovery report about an existing hub; verify the identity during setup. |
| Account-wide plugin sharing | Cursor documents that installed plugins can be available to every bot in an account. Distinct bridge tokens isolate credentials, not a host that exposes every connection to every bot. Prefer per-bot secret/REST setup or truly scoped connections; no isolation claim inside a compromised/shared Cursor account. |
| SSRF / credential forwarding | Webhooks restricted to configured official `https://api2.cursor.sh/automations/webhook/ID`; no redirects or caller-selected URLs, no proxy environment forwarding. Unknown URL shapes fail closed. No arbitrary callback fetch or URL-based token auth. |
| OAuth misuse | Exact allowlisted redirect, fixed client identity, confidential client auth, S256, one-use authorization code, issuer response, resource-bound opaque tokens, token expiration and refresh rotation. Tokens never accepted from query strings. This small single-owner implementation is not a general identity provider. |
| HTTP abuse / resource exhaustion | 128 KiB request limit, 20,000-character message/result limit, bounded polls, 32 request threads, socket timeouts, Origin validation, no path/body/header logging. A TLS reverse proxy must enforce connection/request rates and suppress sensitive query logging. No unlimited public deployment without a proxy. |
| Data retention / host compromise | SQLite contains messages and replies in plaintext; host/volume permissions and disk encryption matter. No automatic deletion in v1. Stop service and back up SQLite consistently; apply your own retention. Anyone with owner credentials can see all fleet jobs. |
| Crash / duplicate action | Durable ledger, atomic mutations, idempotent submission/report, lease recovery; no exactly-once claim for external effects. A crash can lose a wake attempt, never silently mark a task complete. |

## Dependencies and justification

| Dependency | Why |
| --- | --- |
| Python 3.11+ standard library, including SQLite | HTTP, JSON-RPC subset, auth, durable queue, cryptographic randomness, HTTP client and tests; zero PyPI runtime dependencies. Tested here on 3.13. |
| Paid ChatGPT account with Developer mode permitted | Ordinary chat MCP client; Plus/Pro eligibility is documented, workspace restrictions can apply. No OpenAI API billing/key. |
| Cursor account with Grok Bots and working routines/usage | Actual fleet execution, secure bot credentials and optional official wake. A generic Cursor plan name alone is not a guarantee of access. |
| Stable public HTTPS origin reachable by both sides | Cloud clients cannot reach a laptop's localhost. Existing server/proxy or free tunnel can satisfy this; hosting/domain may cost if not already available. |
| Docker (optional) | Packages the same stdlib process in one container. Named volume preserves jobs. No Compose required. |
| `cloudflared` (optional development tunnel) | Quick trial HTTPS without a paid SaaS subscription; temporary URL is unsuitable for permanent one-time setup. |
| MCP Python SDK (optional development check only) | Independently checks wire compatibility; not imported or installed by the bridge. |

## Verification and live acceptance

Automated: real HTTP startup and dry-run smoke, discovery/normal routing/override, actual worker report and polling, token role restrictions, concurrency, stale receipts, leases, durable state, idempotency conflicts, mocked 200/timeout wake semantics, OAuth login/PKCE/replay/refresh/resource rejection, transport/errors and SSRF rejection. No real webhook called.

Local results: 14 automated tests passed on Python 3.13.5; standalone HTTP smoke passed. Independent official MCP Python SDK 2.2.0 passed initialization, tool listing, random discovery, worker claim/report, polling, hub binding and dry-run submission against the actual server. The optional SDK lives in a temporary virtual environment, outside the application. OpenAPI generation also ran successfully.

Operator live acceptance remains necessary: link ChatGPT OAuth over HTTPS; connect each bot's scoped worker; perform random discovery; verify the native saved memory; send two ordinary messages through the hub and one explicit override; watch an actual bot report arrive. Real paid accounts and their current UI were not available in this workspace. Do not describe local tests as a live ChatGPT↔Cursor end-to-end certification. Docker is supplied but the workspace has no Docker executable.

## Evidence (checked 2026-09-30)

The supplied `/workspace/chatgpt-grokbot-bridge.md` is the baseline. Current official pages used for implementation decisions:

- [ChatGPT Developer mode](https://developers.openai.com/api/docs/guides/developer-mode): regular-chat tools, eligibility and supported auth. Static OAuth avoids pretending ChatGPT accepts custom Bearer headers.
- [Connect an MCP plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt): public HTTPS and connection workflow.
- [Plugin authentication](https://developers.openai.com/plugins/build/auth): resource metadata, PKCE, static clients, issuer-aware callbacks and scopes.
- [ChatGPT memory](https://learn.chatgpt.com/docs/customization/memories): web memory/account controls; no bridge-owned memory API.
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports): stateless JSON responses, 202 notifications and 405 when SSE is not offered.
- [Cursor routines](https://cursor.com/help/grok-bot/routines): official webhook and HTTP 200 acceptance semantics.
- [Cursor secrets](https://cursor.com/help/grok-bot/secrets) and [plugins](https://cursor.com/help/grok-bot/connect-plugins): secure inputs and account-level connection sharing.

The shared-ledger design and working code are this project's composition; those pages do not advertise a native ChatGPT↔Grok Bot integration.
