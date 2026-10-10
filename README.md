# Barbershop Telegram Booking Notification Bot

Production-ready Telegram notification service for **The Kings** barbershop. It receives booking events from an existing website over a secure HTTP webhook and sends formatted alerts to a configured admin/staff Telegram chat.

This project does **not** replace the website booking system. Customers book on the website; this bot only notifies staff.

## Workspace note

This repository currently contains **only the notification bot**. No website source code was present in the workspace, so website integration must be added on the website backend using the examples below once that stack is available.

## Features

- Secure `POST /api/webhooks/bookings` endpoint with Bearer shared-secret auth
- Pydantic validation for booking payloads
- HTML Telegram notifications with proper escaping
- SQLite persistence of event IDs and delivery status
- Duplicate event protection (safe under concurrent retries)
- Background worker with exponential backoff for Telegram outages
- Jobs survive process restarts (DB-backed, not memory-only)
- `/start` command for internal staff explanation only
- Health check, Docker Compose deployment, and automated tests

## Project structure

```text
app/
  main.py                      # FastAPI app, lifespan, middleware
  config.py                    # Environment settings
  bot.py                       # aiogram /start handler
  api/
    routes.py                  # /health and webhook routes
    schemas.py                 # Request/response models
  services/
    notifications.py           # Message formatting + Telegram send
  db/
    database.py                # Async SQLite access
    models.py                  # Notification status models
  workers/
    notification_worker.py     # Persistent delivery worker
tests/
  test_webhook.py
  test_notifications.py
examples/
  send_booking_webhook.py
  send_booking_webhook.js
.env.example
Dockerfile
docker-compose.yml
requirements.txt
```

## Quick setup (local)

### 1. Create a Telegram bot (BotFather)

1. Open Telegram and chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts.
3. Copy the bot token (looks like `123456:ABC-DEF...`).
4. Keep this token private. Never commit it.

### 2. Get the administrator chat ID

1. Start a chat with your new bot and send `/start`.
2. Or add the bot to a private staff group and send a message.
3. Open:
   `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Find `"chat":{"id": ...}` and copy that numeric ID.
   - Private chat IDs are usually positive.
   - Group chat IDs are usually negative.
5. Set `TELEGRAM_CHAT_ID` to that value.

Only this configured chat receives booking notifications.

### 3. Configure environment

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # Linux/macOS
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF_your_bot_token_here
TELEGRAM_CHAT_ID=123456789
WEBHOOK_SECRET=replace_with_a_long_random_secret
DATABASE_URL=sqlite:///./data/barbershop.db
TIMEZONE=Asia/Tashkent
LOG_LEVEL=INFO
```

Generate a strong webhook secret, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Create the data directory:

```bash
mkdir data
```

### 4. Run the service

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Example webhook request

```bash
curl -X POST http://127.0.0.1:8000/api/webhooks/bookings \
  -H "Authorization: Bearer replace_with_a_long_random_secret" \
  -H "Content-Type: application/json" \
  -d "{
    \"event_id\": \"evt_12345\",
    \"event_type\": \"booking.created\",
    \"booking\": {
      \"id\": \"12345\",
      \"customer_name\": \"John Smith\",
      \"customer_phone\": \"+998901234567\",
      \"service_name\": \"Haircut\",
      \"barber_name\": \"Aziz\",
      \"date\": \"2026-10-15\",
      \"time\": \"14:30\",
      \"price\": 100000,
      \"currency\": \"UZS\",
      \"status\": \"confirmed\"
    }
  }"
```

Successful response:

```json
{
  "ok": true,
  "event_id": "evt_12345",
  "status": "accepted"
}
```

Duplicate retry response:

```json
{
  "ok": true,
  "event_id": "evt_12345",
  "status": "duplicate"
}
```

Supported `event_type` values:

- `booking.created`
- `booking.confirmed`

Required booking fields:

- `id`
- `customer_name`

Optional fields are omitted from the Telegram message when absent.

## Connecting the existing website

Call the webhook **only from the website backend**, after the booking is successfully saved and confirmed.

Do **not**:

- Call the webhook from browser/frontend JavaScript
- Put `WEBHOOK_SECRET` in public env vars (`NEXT_PUBLIC_*`, Vite `VITE_*`, etc.)
- Put the secret in URLs/query strings
- Notify for bookings that failed to save

Recommended website env vars:

```env
BOOKING_BOT_WEBHOOK_URL=https://bot.yourdomain.com/api/webhooks/bookings
BOOKING_BOT_WEBHOOK_SECRET=same_secret_as_bot_WEBHOOK_SECRET
```

Use a **stable `event_id`** such as `booking_created_<booking_id>` so website retries do not create duplicate Telegram messages. This bot also deduplicates by `event_id` in SQLite.

Integration examples:

- Python: `examples/send_booking_webhook.py`
- Node.js: `examples/send_booking_webhook.js`

Pseudo-flow for any backend:

1. Save booking in the existing database.
2. If save succeeds, POST the mapped payload to the bot webhook.
3. Use a short timeout (e.g. 5 seconds) and a few safe retries on network errors.
4. Continue the normal booking success response to the customer even if notification delivery is delayed; the bot worker will retry Telegram separately.

## Docker Compose deployment (Linux VPS)

### Install

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
git clone <your-repo-url> tg_bot_thekings_barbershop
cd tg_bot_thekings_barbershop
cp .env.example .env
nano .env
mkdir -p data
```

### Start / stop / restart

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f booking-bot
docker compose restart booking-bot
docker compose down
```

SQLite persistence: `./data` is mounted to `/app/data` in the container, so the database survives restarts.

### HTTPS with Caddy (recommended)

Install Caddy and proxy to the local bot:

```caddy
bot.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```

Or Nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name bot.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/bot.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.yourdomain.com/privkey.pem;

    client_max_body_size 64k;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Protect secrets:

- Keep `.env` readable only by the deploy user (`chmod 600 .env`)
- Do not commit `.env`
- Do not expose Docker/debug ports publicly beyond 80/443
- Prefer firewalling the host and exposing only the reverse proxy

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

Tests mock Telegram API calls and do not require real bot credentials.

## Security notes

- Webhook auth uses `Authorization: Bearer <WEBHOOK_SECRET>`
- Unauthorized requests receive HTTP 401
- Secrets are never hardcoded
- Request body size is limited
- Customer/text values are HTML-escaped before Telegram send
- Notifications are sent only to `TELEGRAM_CHAT_ID`
- Logs include event IDs and statuses, not bot tokens/secrets

## Remaining manual configuration

1. Create the Telegram bot with BotFather and set `TELEGRAM_BOT_TOKEN`
2. Determine the admin/staff chat ID and set `TELEGRAM_CHAT_ID`
3. Generate and set a strong `WEBHOOK_SECRET`
4. Deploy the bot behind HTTPS
5. Add the server-side webhook call to the existing website booking success path
6. Verify with one real test booking
