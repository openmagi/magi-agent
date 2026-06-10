# Channels

> **Note — default-off / shadow today.** Channel code (Telegram, Discord) ships in shadow / local-fake mode; live send/receive is gated and produces intent + receipt records, not real delivery (`magi_agent/channels/telegram_adapter.py`, `contract.py`).

Channels are how an agent run reaches an outside surface — Telegram, Discord,
the web chat, or the mobile app. The channel layer is the side-effect boundary
between a run and a user-visible destination. Today it is wired for inspection
and projection but **delivery is not attached**: the adapters validate, redact,
and record what *would* be sent rather than sending it.

## Architecture: adapter → boundary → dispatcher

The channel surface is built from three cooperating pieces under
`magi_agent/channels/`:

- **Adapter** (`telegram_adapter.py`, `discord_adapter.py`) — owns the
  per-provider request/response shapes and the safety checks for one channel
  type. It decides a `status` for every operation (see below) and emits a
  decision record instead of performing live I/O.
- **Boundary** (`telegram_boundary.py`, `runtime_boundary.py`) — the scoped
  authority gate. It carries digested identifiers (bot / owner / session) and
  the authority flags that are all locked to `False` by default
  (`production_channel_write`, `channel_delivery_performed`, `route_attached`,
  …). Nothing past this boundary is allowed to attach live traffic.
- **Dispatcher** (`dispatcher.py`) — routes a delivery request to the right
  adapter and records a `ChannelDispatchStatus` of `disabled`, `blocked`,
  `recorded_local_fake`, or `error`. The happy path today is
  `recorded_local_fake`.

A channel is named by a **`ChannelRef`** (`contract.py`): a `type`
(`ChannelType` = `"web" | "app" | "telegram" | "discord"`) plus a non-empty
`channel_id`. Delivery requests (`ChannelDeliveryRequest`) and their results
(`ChannelDeliveryReceipt`, with `DeliveryStatus` = `queued | sent | failed |
skipped`) are frozen Pydantic models, so a recorded receipt is an immutable
audit record.

`contract.py` also declares the per-channel manifests
(`ChannelAdapterManifest`). Each manifest reports its capabilities — for
Telegram: `supports_polling=True`, `supports_stale_webhook_mitigation=True`,
`max_text_chars=4096`. A model validator on the manifest **enforces** that every
channel manifest is `default_enabled=False`, `traffic_attached=False`, and
`execution_attached=False`; constructing one with those flags on raises. That is
the structural reason channels are default-off, not just a runtime setting.

## Telegram

> **Note — default-off / shadow today.** `telegram_adapter.py` runs in shadow / local-fake mode.

### How a Telegram channel is referenced

A Telegram destination is a `ChannelRef(type="telegram", channel_id=...)`.
Outbound work is described by a `TelegramSendRequest` (operation =
`send_message | send_document | send_photo | send_typing`, plus `chat_id`,
optional `text`, `reply_to_message_id`, `file_ref`, `artifact_receipt_ref`).
Inbound polling uses `TelegramPollRequest`; file pulls use
`TelegramDownloadRequest`.

The adapter is configured by `TelegramAdapterConfig`. The fields it actually
consumes include `enabled` (default `False`), `local_fake_provider_enabled`
(default `False`), `selected_channel_routes`, `provider_allowlist`, and
`download_enabled`. Critically, the "go live" fields —
`production_channel_write_enabled`, `telegram_polling_attached`,
`telegram_attached`, `telegram_webhook_mitigation_attached`, `route_attached` —
are typed as `Literal[False]`. They cannot be set to `True` through this config:
`model_copy` re-forces them to `False` on every copy. Live delivery is therefore
gated behind code paths that do not exist in the open surface, not behind a flag
you can flip in config.

The bot token / chat reference are handled as **digested, scoped** values
(`bot_id_digest`, `owner_id_digest`, `session_key_digest`, `chat_id`) rather
than being echoed back; the adapter never returns a raw token.

### What the adapter does today

For each operation the adapter returns one of the `TelegramAdapterStatus`
values defined in `telegram_adapter.py`:

`disabled`, `blocked`, `poll_intent`, `inbound_projected_local_fake`,
`send_intent`, `sent_local_fake`, `typing_recorded_local_fake`,
`download_intent`, `download_recorded_local_fake`,
`webhook_mitigation_intent`, `provider_error_swallowed`.

The `*_local_fake` and `*_intent` statuses are the tell: on the happy path the
adapter **records an intent or a fake receipt** — e.g. `sent_local_fake` with a
diagnostic of `local_fake_telegram_send_receipt_only` — and never calls a live
Telegram API. Inbound updates are surfaced as
`inbound_projected_local_fake` (a projection of a poll, not a live poll).

Concretely, today the adapter:

- **Validates** the channel ref, operation, chat id, and download MIME type
  (downloads are restricted to an allowlist such as `application/pdf`,
  `text/csv`, `image/png`).
- **Projects inbound** Telegram updates as `inbound_projected_local_fake`
  records instead of consuming a live update stream.
- **Records send intents / fake receipts** for outbound messages, documents,
  photos, and typing — producing an auditable record of what *would* be sent.
- **Redacts secrets and private paths.** The adapter carries dedicated regexes
  (`_SECRET_TEXT_RE`, `_PRIVATE_TEXT_RE`, `_SENSITIVE_QUERY_RE`,
  `_PRIVATE_OBJECT_HOST_RE`) and will **block** outbound text that contains
  tokens (Bearer / GitHub PAT / Slack / AWS / Google / `sk-…` keys, Telegram
  bot tokens), private filesystem paths (`/Users`, `/home`, `/workspace`,
  `/data/bots`), raw transcripts / chain-of-thought, or signed object-store
  URLs. This redaction is a safety feature that survives even when delivery is
  later attached.

### Live self-host delivery (gated, default-off)

The **audit boundary** (`telegram_adapter.py`) stays shadow-only by design — its
`Literal[False]` authority flags can never be flipped, so it always produces a
local-fake receipt. Live delivery does **not** route through that boundary;
instead a self-host operator opts into a separate live path:

- **Concrete provider** — `magi_agent/channels/providers/telegram_httpx.py`
  (`TelegramHttpxProvider`) is the only module that constructs a real HTTP
  client. It implements the injected `TelegramLiveProviderPort`
  (`getUpdates` / `sendMessage` / `deleteWebhook`) and reports
  `openmagi_local_fake_provider = False` — it never masquerades as the audit
  fake.
- **Operator wiring** — `magi_agent/gateway/channel_watchers.py` reads the live
  gate and bot token from the environment, constructs the provider, and wraps a
  `poll_once` closure in `build_channel_poll_watcher` for the gateway daemon to
  supervise. It is **fail-closed**: with the gate off (or no token) it builds no
  provider and returns `None`.
- **Receipt** — live sends record an honest receipt in the wiring layer
  (`provider_called=True`, `channel_delivery_performed=True`), separate from the
  boundary's locked-`False` flags.

Activation (self-host only):

```
MAGI_GATEWAY_DAEMON_ENABLED=1 \
MAGI_CHANNEL_LIVE_TELEGRAM=1 \
MAGI_TELEGRAM_BOT_TOKEN=<bot-token> \
magi gateway start
```

On startup the watcher calls `deleteWebhook` once to clear any stale webhook
(a set webhook makes `getUpdates` return HTTP 409). The `[SILENT]` contract is
honored: an outbound message that is exactly `[SILENT]` is suppressed without
calling the provider.

> **Self-host only — do not dual-run.** A managed service deployment already runs a
> separate Telegram long-poller; running this daemon channel watcher alongside
> it causes Telegram 409 conflicts on `getUpdates`. Enable this watcher only on
> self-host deployments, not on the hosted path.

## Discord

> **Note — default-off / shadow today.** Same shadow posture as Telegram.

`discord_adapter.py` follows the same adapter → boundary → dispatcher pattern,
the same default-off manifest invariant (`max_text_chars=2000`), and the same
record-intent-instead-of-deliver behavior with secret / private-path redaction.
Treat it as shadow until live delivery authority is attached.

## See also

- [what-works-today.md](what-works-today.md) — what is live vs shadow vs planned.
- [integrations.md](integrations.md) — side-effect boundary philosophy.
- [boundaries.md](boundaries.md) — the authority-flag model channels build on.
