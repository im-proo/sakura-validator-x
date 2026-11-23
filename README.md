# 𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗

◈ Tʜᴇ ˹Nʏʀᴏ ꭙ Bᴏᴛs !! ♡

A Telegram bot that checks Netflix cookie files against one or more
[nfcheck](https://github.com/im-proo/nfcheck-api) checker instances, delivers
every hit as a ready-to-use account card, and manages plans, keys, daily
limits, force-join and an admin panel out of MongoDB.

The bot never parses cookies itself. It uploads the file to a checker
instance, polls the job, streams the hits back to the user, and deletes the
upload when the job ends.

---

## Features

- **Upload anything the checker understands** — `.txt`, `.json`, `.zip`, `.rar`
  (renamed or nested archives included; the checker sniffs the magic bytes).
- **Live dashboard** — progress bar, checked / hits / premium / free / errors,
  live CPM, cancel button, 3-second edit cadence to stay inside Telegram limits.
- **Hit delivery** — one rich card per hit with the account details, the cookie
  file and Phone / PC / TV login buttons; hits optionally mirrored to a channel.
- **Result bundles** — at the end of a run, `premium_accounts.zip` plus
  `premium_accounts.txt`.
- **Plans & keys** — free / premium tiers with per-plan cookie caps, daily
  attempt limits, one-day free trials and redeemable keys.
- **Multiple checker instances** — round-robin / priority / random rotation,
  health monitor, capacity reservation and automatic failover.
- **Admin panel** — users, plans, keys, API instances, channels, videos,
  broadcast, runtime config and logs.
- **Health endpoint** — binds `0.0.0.0:$PORT` with `GET /health` so hosting
  platforms and uptime monitors see a live service.
- **Hardcoded configuration** — two environment variables, everything else in
  `config.py`. JSON logs by default.

## Requirements

- Python 3.10+
- MongoDB 5+ (local or Atlas)
- One or more [nfcheck-api](https://github.com/im-proo/nfcheck-api) instances

## Quick start

```bash
git clone https://github.com/im-proo/sakura-validator-x
cd sakura-validator-x

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # then edit it
.venv/bin/python bot.py
```

`.env` (or the process environment) needs **exactly two values**:

```dotenv
BOT_TOKEN=123456:ABC-your-token
MONGO_URI=mongodb://127.0.0.1:27017/sakura_validator_x
```

- A database named in the `MONGO_URI` path is used as-is.
- Without a path, the bot uses `MONGO_DB_DEFAULT` from `config.py`.

Everything else — owner id, admins, worker count, rate limit, rotation mode,
log format, temp directory — is a constant in [`config.py`](config.py). Edit
it, restart the bot, done. No other environment variables are read.

### Docker

```bash
docker build -t sakura-validator-x .
docker run -d --name sakura \
  -p 8080:8080 \
  -e BOT_TOKEN=... \
  -e MONGO_URI=mongodb://host.docker.internal:27017/sakura_validator_x \
  sakura-validator-x
```

## Keeping it online 24/7

The bot binds a small HTTP server on `0.0.0.0:$PORT` (`PORT` defaults to
`8080`) when it starts, and closes it on shutdown. That port is what hosting
platforms probe; long polling alone opens no port, which is why a platform
logs *"no open ports detected"* and stops the service.

| What | Value |
| --- | --- |
| Health URL (local) | `http://127.0.0.1:8080/health` |
| Health URL (deployed) | `https://<your-app-host>/health` |
| Alternative | `https://<your-app-host>/` (same JSON) |
| Expected response | `200` with `{"status": "ok", ...}` |

With UptimeRobot: **Add New Monitor → HTTP(s)** → URL
`https://<your-app-host>/health` → interval **5 minutes** → keyword
`"status": "ok"` (optional, catches a port that answers but a bot that is
half-dead is not covered — Telegram polling is not part of the probe).

Checklist for a real 24/7 deployment:

- Enable **public networking** on the platform and make sure it routes to the
  same `PORT` the process was given (do not hardcode a different one).
- Use a hosted MongoDB (`MONGO_URI=mongodb+srv://…`); `127.0.0.1` only exists
  inside the container and will be lost on restart.
- Keep the checker API reachable from the host, or the bot starts fine but
  every check fails with `no checker instance available`.
- One instance only: Telegram long polling allows a single `getUpdates`
  consumer, so two replicas will fight over updates.


## Configuration (`config.py`)

| Constant | Default | Meaning |
| --- | --- | --- |
| `OWNER_ID` | `8900061448` | Owner: bypasses force-join, sees the admin panel |
| `ADMIN_IDS` | `(8900061448,)` | Extra admins with the same powers |
| `FORCE_JOIN_CHANNELS` | four `-100…` ids | Channels always gated, even when MongoDB has no row for them |
| `FORCE_JOIN_LINKS` | one known link | Per-channel join link used when Telegram cannot resolve one; every gated channel is always shown as a button |
| `MONGO_DB_DEFAULT` | `sakura_validator_x` | Database when the URI has no path |
| `MAX_CONCURRENT_GLOBAL` | `50` | Check sessions allowed at once, all users together |
| `CHECKER_CONCURRENCY` | `300` | Workers requested per job (`0` = use the API default) |
| `CHECKER_RATE_PER_MINUTE` | `0` | Per-job rate cap (`0` = unlimited) |
| `API_ROTATION_MODE` | `round_robin` | `round_robin`, `priority` or `random` |
| `API_TIMEOUT_SECONDS` | `30` | Health-check timeout per instance |
| `LOG_LEVEL` | `INFO` | Log level |
| `LOG_JSON` | `True` | One JSON object per log line |
| `TEMP_DIR` | `/tmp/sakura` | Uploads and result files |
| `HIT_FORWARD_CHANNEL` | `""` | Default hit mirror channel (admin panel can change it) |

Runtime values that admins should tune without a redeploy (rotation mode,
concurrency cap, videos, forward channel, maintenance mode) live in the
MongoDB `bot_config` collection and are editable from the admin panel.

## Adding checker instances

The bot stores instances in MongoDB (`apis` collection):

| Field | Example |
| --- | --- |
| `api_id` | `api-01` |
| `name` | `local` |
| `url` | `http://127.0.0.1:8080` |
| `status` | `online` |
| `priority` | `1` |

Use **Admin → APIs** to add, edit, enable/disable and test them, or insert
documents directly.

## Project layout

```
bot.py                     entry point: logging, dispatcher, polling
config.py                  all configuration (2 env vars + constants)
core/
  checker_api.py           HTTP client for nfcheck jobs / hits / stats
  api_manager.py           instance rotation, health monitor, capacity
  database.py              motor collections, config defaults, audit log
  middlewares.py           ban, maintenance and force-join gates
  scheduler.py             daily resets, housekeeping, stats snapshots
handlers/                  user commands and the admin panel
services/
  file_service.py          upload intake, hit text and result bundles
  checker_service.py       job submit → poll → deliver → finalize
  plan_service.py          plans, attempts, daily limits, trials
  key_service.py           redeemable keys
models/schemas.py          shared types and unit helpers
ui/                        typography, renderer, keyboards, FSM states
tests/                     pytest suite
```

## Tests

```bash
.venv/bin/pytest tests/
```

Mongo-backed tests skip automatically when no `mongod` is reachable.

## How a check runs

1. `/check` → instructions → user sends a file.
2. The bot downloads it, rejects only what is *certainly* unusable (empty
   file, or plain text with no Netflix data at all) and enforces the plan cap.
3. User picks hit mode (all hits or premium only).
4. The file is posted to a checker instance as a multipart job; one attempt is
   spent only after the API accepts it.
5. The bot polls every 3 seconds, edits the live card, and sends each new hit
   as its own message with the cookie file attached.
6. On done/cancel/failed the summary card, the ZIP and the TXT are delivered,
   the instance is released and the upload is deleted.

## Troubleshooting

- **"this file has no Netflix cookies inside it"** — the file really is plain
  text with no Netflix data (checked in full). Archives, renamed archives,
  nested archives and unreadable containers are always forwarded to the
  checker and judged there.
- **Jobs fail with `no Netflix cookies found in "<file>"`** — the checker
  parsed the file and found no `NetflixId`; export the cookies again.
- **No hits at all** — a checker without proxies gets blocked by Netflix
  quickly. Upload residential/ISP proxies to the instance.
- **`no checker instance available`** — every instance is offline or at
  capacity. Check **Admin → APIs** and the instance `/health` endpoint.
- **Logs** are JSON lines; `docker logs` or `journalctl -u sakura -o cat`
  show them unchanged.
