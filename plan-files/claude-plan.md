# Strava → Garmin Connect Auto-Sync Backend

## Project overview

A Python backend service that listens for new cycling activities uploaded to Strava via webhooks, downloads the original activity file (preferring .FIT format), and automatically uploads it to Garmin Connect.

---

## Architecture

```
Strava (new cycling activity uploaded)
    │
    ▼  POST webhook event (object_type, object_id, owner_id)
    │
┌───────────────────────────────────────────────┐
│  Python Backend (FastAPI)                      │
│                                                │
│  1. /webhook GET  — handle Strava validation   │
│  2. /webhook POST — receive activity events    │
│  3. Filter: cycling only, aspect_type=create   │
│  4. Background task:                           │
│     a. Download original .FIT via web scrape   │
│        (fallback: Streams API → build GPX)     │
│     b. Upload to Garmin Connect via            │
│        python-garminconnect import_activity()  │
│  5. /health GET — health check endpoint        │
│  6. /auth/strava GET — OAuth callback          │
└───────────────────────────────────────────────┘
    │
    ▼
Garmin Connect (activity appears, no re-export to Strava)
```

---

## Tech stack

| Component | Choice | Reason |
|---|---|---|
| Framework | **FastAPI** | Async-native, background tasks built in, auto-generated docs |
| Python version | **3.11+** | Required by garminconnect library |
| Strava GPX fallback | **strava2gpx** | Builds GPX from Strava Streams API |
| Garmin upload | **python-garminconnect** (>=0.3.1) | `import_activity()` uses import endpoint so activities won't re-export to Strava |
| Web scraping (FIT) | **httpx** | Async HTTP client for scraping `export_original` from Strava website |
| Task queue (optional) | **Celery + Redis** or **FastAPI BackgroundTasks** | Webhook must respond within 2 seconds; processing happens async |
| Database | **SQLite** (via **aiosqlite**) | Lightweight, stores tokens and processed activity IDs |
| Config/secrets | **pydantic-settings** | Loads from `.env` file with validation |
| Deployment | **Docker** + any cloud with HTTPS (Railway, Fly.io, VPS + Caddy) | Strava requires HTTPS callback URL |

---

## Project structure

```
strava-to-garmin/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app entry point, lifespan events
│   ├── config.py               # Settings from .env via pydantic-settings
│   ├── database.py             # SQLite setup, token + activity log tables
│   ├── models.py               # Pydantic models for webhook payloads, etc.
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── webhook.py          # GET validation + POST event handler
│   │   ├── auth.py             # Strava OAuth callback flow
│   │   └── health.py           # Health check endpoint
│   ├── services/
│   │   ├── __init__.py
│   │   ├── strava_auth.py      # Token refresh, OAuth helpers
│   │   ├── strava_download.py  # Download .FIT (web scrape) or build GPX (streams)
│   │   ├── garmin_upload.py    # Garmin Connect auth, token persistence, upload
│   │   ├── notifications.py   # Telegram notification service
│   │   └── polling.py         # Fallback: poll Strava API for missed activities
│   └── tasks/
│       ├── __init__.py
│       └── sync_activity.py    # Main background task: download → upload pipeline
├── scripts/
│   ├── setup_webhook.py        # One-time: create Strava webhook subscription
│   ├── setup_strava_auth.py    # One-time: walk through OAuth flow, get refresh token
│   └── setup_garmin_auth.py    # One-time: login to Garmin, save tokens
├── tests/
│   ├── test_webhook.py
│   ├── test_strava_download.py
│   └── test_garmin_upload.py
├── .env.example
├── render.yaml                 # Render deployment config (infra-as-code)
├── requirements.txt
└── README.md
```

---

## Implementation plan

Build in this order. Each phase is independently testable.

### Phase 1: Project scaffolding and config

**Files:** `config.py`, `.env.example`, `requirements.txt`, `main.py`

1. Create `requirements.txt`:
   ```
   fastapi>=0.110.0
   uvicorn[standard]>=0.27.0
   httpx>=0.27.0
   python-dotenv>=1.0.0
   pydantic-settings>=2.0.0
   garminconnect>=0.3.1
   strava2gpx>=0.3.0
   aiosqlite>=0.20.0
   beautifulsoup4>=4.12.0
   ```

2. Create `config.py` using pydantic-settings:
   ```python
   class Settings(BaseSettings):
       # Strava API
       STRAVA_CLIENT_ID: str
       STRAVA_CLIENT_SECRET: str
       STRAVA_VERIFY_TOKEN: str  # random string for webhook validation
       STRAVA_REFRESH_TOKEN: str  # obtained from OAuth flow

       # Strava web session (for FIT download scraping)
       STRAVA_EMAIL: str
       STRAVA_PASSWORD: str

       # Garmin Connect
       GARMIN_EMAIL: str
       GARMIN_PASSWORD: str

       # App
       DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"
       LOG_LEVEL: str = "INFO"
       ACTIVITY_TYPES_TO_SYNC: list[str] = ["Ride", "VirtualRide", "EBikeRide"]

       model_config = SettingsConfigDict(env_file=".env")
   ```

3. Create `.env.example` with all keys (no values).

4. Create bare `main.py` with FastAPI app, include routers, add lifespan for DB init.

### Phase 2: Database layer

**Files:** `database.py`, `models.py`

1. Create SQLite tables:
   - `strava_tokens`: `id`, `access_token`, `refresh_token`, `expires_at`, `updated_at`
   - `garmin_tokens`: `id`, `token_json` (serialized garminconnect token blob), `updated_at`
   - `synced_activities`: `id`, `strava_activity_id` (unique), `activity_name`, `activity_type`, `file_format` (fit/gpx), `garmin_activity_id`, `status` (pending/success/failed), `error_message`, `created_at`

2. Create Pydantic models:
   ```python
   class StravaWebhookEvent(BaseModel):
       object_type: str        # "activity" or "athlete"
       object_id: int          # activity ID
       aspect_type: str        # "create", "update", "delete"
       owner_id: int           # athlete ID
       subscription_id: int
       updates: dict = {}
       event_time: int

   class StravaWebhookValidation(BaseModel):
       hub_mode: str = Field(alias="hub.mode")
       hub_verify_token: str = Field(alias="hub.verify_token")
       hub_challenge: str = Field(alias="hub.challenge")
   ```

### Phase 3: Strava OAuth and token management

**Files:** `services/strava_auth.py`, `routers/auth.py`, `scripts/setup_strava_auth.py`

1. **Setup script** (`scripts/setup_strava_auth.py`):
   - Opens browser to `https://www.strava.com/oauth/authorize` with params:
     - `client_id`, `redirect_uri=http://localhost:8000/auth/strava`
     - `response_type=code`
     - `scope=activity:read_all`
     - `approval_prompt=auto`
   - User authorizes → Strava redirects to callback with `code`
   - Exchange `code` for `access_token` + `refresh_token` via POST to `https://www.strava.com/oauth/token`
   - Save refresh_token to `.env` and database

2. **Token refresh service** (`services/strava_auth.py`):
   ```python
   async def get_valid_access_token() -> str:
       # Check if current token is expired
       # If expired, POST to https://www.strava.com/oauth/token with:
       #   client_id, client_secret, grant_type=refresh_token, refresh_token
       # Save new access_token, refresh_token, expires_at to DB
       # Return valid access_token
   ```

3. **Auth callback route** (`routers/auth.py`):
   - `GET /auth/strava` — receives OAuth code, exchanges for tokens, stores them

### Phase 4: Webhook endpoint

**Files:** `routers/webhook.py`

1. **GET /webhook** — Strava subscription validation:
   ```python
   @router.get("/webhook")
   async def verify_webhook(request: Request):
       mode = request.query_params.get("hub.mode")
       token = request.query_params.get("hub.verify_token")
       challenge = request.query_params.get("hub.challenge")

       if mode == "subscribe" and token == settings.STRAVA_VERIFY_TOKEN:
           return {"hub.challenge": challenge}
       raise HTTPException(403)
   ```

2. **POST /webhook** — Receive events:
   ```python
   @router.post("/webhook")
   async def receive_webhook(event: StravaWebhookEvent, background_tasks: BackgroundTasks):
       # MUST respond within 2 seconds — do minimal work here

       # Validate subscription_id matches ours
       # Filter: only process activity creates
       if event.object_type != "activity" or event.aspect_type != "create":
           return {"status": "ignored"}

       # Check if already processed (idempotency)
       if await is_already_synced(event.object_id):
           return {"status": "already_synced"}

       # Queue background task
       background_tasks.add_task(
           sync_activity_task,
           strava_activity_id=event.object_id,
           owner_id=event.owner_id
       )
       return {"status": "processing"}
   ```

### Phase 5: Strava activity download

**Files:** `services/strava_download.py`

This is the most complex service. Two strategies with automatic fallback.

#### Strategy A — Web scrape original .FIT file (preferred)

1. Maintain an authenticated web session with Strava using `httpx.AsyncClient`:
   ```python
   async def get_strava_web_session() -> httpx.AsyncClient:
       # POST https://www.strava.com/session with email + password
       # Or reuse existing session cookies if still valid
       # Store/load cookies from file or DB
   ```

2. Download original file:
   ```python
   async def download_original_file(activity_id: int) -> tuple[bytes, str]:
       session = await get_strava_web_session()
       url = f"https://www.strava.com/activities/{activity_id}/export_original"
       response = await session.get(url, follow_redirects=True)

       # Determine file format from Content-Disposition header
       # e.g. "attachment; filename=activity_123456.fit"
       content_disp = response.headers.get("content-disposition", "")
       filename = parse_filename(content_disp)
       extension = Path(filename).suffix  # .fit, .gpx, .tcx, .json

       return response.content, extension
   ```

3. Handle edge cases:
   - Session expired → re-login
   - Strava returns HTML instead of file → session invalid, retry
   - Activity is private → ensure `activity:read_all` scope
   - Original format is .json (Strava mobile app) → fall back to Strategy B

#### Strategy B — Streams API → GPX (fallback)

1. Use `strava2gpx` library:
   ```python
   async def download_as_gpx(activity_id: int) -> bytes:
       from strava2gpx import strava2gpx

       s2g = strava2gpx(
           settings.STRAVA_CLIENT_ID,
           settings.STRAVA_CLIENT_SECRET,
           await get_refresh_token()
       )
       await s2g.connect()

       # Write to temp file, read back
       tmp_path = f"/tmp/strava_{activity_id}"
       await s2g.write_to_gpx(activity_id, tmp_path)

       gpx_path = f"{tmp_path}.gpx"
       with open(gpx_path, "rb") as f:
           return f.read()
   ```

2. This captures: GPS coordinates, timestamps, heart rate, cadence, power, temperature.
3. This loses: laps, device metadata, distance calculations, workout structure.

#### Download orchestrator

```python
async def download_activity(activity_id: int) -> tuple[bytes, str]:
    """
    Try to download original .FIT file first.
    Fall back to building GPX from Streams API.
    Returns (file_bytes, file_extension).
    """
    try:
        file_bytes, ext = await download_original_file(activity_id)
        if ext in (".fit", ".tcx", ".gpx"):
            logger.info(f"Downloaded original {ext} for activity {activity_id}")
            return file_bytes, ext
        # Original is .json (Strava mobile) — not useful, fall through
    except Exception as e:
        logger.warning(f"Web scrape failed for {activity_id}: {e}")

    # Fallback: build GPX from Streams API
    logger.info(f"Falling back to Streams API for activity {activity_id}")
    gpx_bytes = await download_as_gpx(activity_id)
    return gpx_bytes, ".gpx"
```

### Phase 6: Strava activity type filtering

**Files:** inside `tasks/sync_activity.py`

Before downloading, verify the activity is a cycling type:

```python
async def get_activity_type(activity_id: int) -> str:
    """Fetch activity details from Strava API to check sport type."""
    access_token = await get_valid_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("sport_type", data.get("type", "Unknown"))

# In the sync task:
activity_type = await get_activity_type(strava_activity_id)
if activity_type not in settings.ACTIVITY_TYPES_TO_SYNC:
    logger.info(f"Skipping non-cycling activity {strava_activity_id}: {activity_type}")
    await update_activity_status(strava_activity_id, "skipped")
    return
```

Valid cycling types to filter for: `Ride`, `VirtualRide`, `EBikeRide`, `Handcycle`, `Velomobile`. Configure via `ACTIVITY_TYPES_TO_SYNC` in settings.

### Phase 7: Garmin Connect upload

**Files:** `services/garmin_upload.py`, `scripts/setup_garmin_auth.py`

1. **Initial setup script** (`scripts/setup_garmin_auth.py`):
   ```python
   from garminconnect import Garmin

   def setup():
       email = input("Garmin email: ")
       password = input("Garmin password: ")

       garmin = Garmin(email, password)
       garmin.login()
       # If MFA enabled, prompt_mfa callback handles it

       # Tokens auto-saved to ~/.garminconnect/garmin_tokens.json
       print("Garmin auth successful. Tokens saved.")
   ```

2. **Upload service** (`services/garmin_upload.py`):
   ```python
   from garminconnect import Garmin

   _garmin_client: Garmin | None = None

   async def get_garmin_client() -> Garmin:
       global _garmin_client
       if _garmin_client is None:
           _garmin_client = Garmin(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD)
           _garmin_client.login()
           # Tokens auto-refresh via DI OAuth Bearer tokens
       return _garmin_client

   async def upload_to_garmin(file_bytes: bytes, file_extension: str) -> dict:
       """
       Upload activity file to Garmin Connect.
       Uses import_activity() which treats uploads as imports,
       NOT device-synced — so Garmin won't re-export to Strava.
       """
       client = await get_garmin_client()

       # Write temp file (garminconnect expects a file path)
       tmp_path = f"/tmp/garmin_upload{file_extension}"
       with open(tmp_path, "wb") as f:
           f.write(file_bytes)

       try:
           result = client.import_activity(tmp_path)
           return result
       except Exception as e:
           # Handle: auth expired → re-login and retry once
           if "401" in str(e) or "expired" in str(e).lower():
               _garmin_client = None
               client = await get_garmin_client()
               result = client.import_activity(tmp_path)
               return result
           raise
       finally:
           Path(tmp_path).unlink(missing_ok=True)
   ```

3. Key considerations:
   - `import_activity()` vs `upload_activity()`: use **import_activity** — it hits Garmin's import endpoint with mobile app headers, so the activity is treated as an import and **will not be re-exported to third parties like Strava** (avoids sync loops).
   - If activity already exists on Garmin, the upload returns HTTP 409 (duplicate). Handle gracefully.
   - Garmin auth uses DI OAuth Bearer tokens that auto-refresh. A full re-login (with possible MFA) is only needed if the refresh token itself expires.
   - Token file location: `~/.garminconnect/garmin_tokens.json`.

### Phase 8: Main sync task (orchestrator)

**Files:** `tasks/sync_activity.py`

```python
async def sync_activity_task(strava_activity_id: int, owner_id: int):
    """
    Complete pipeline: validate → download → upload → log.
    This runs as a background task, not blocking the webhook response.
    """
    try:
        # 1. Record as pending
        await create_activity_record(strava_activity_id, status="pending")

        # 2. Check activity type
        activity_type = await get_activity_type(strava_activity_id)
        if activity_type not in settings.ACTIVITY_TYPES_TO_SYNC:
            await update_activity_status(strava_activity_id, "skipped",
                                         note=f"Type: {activity_type}")
            return

        # 3. Download activity file
        file_bytes, file_ext = await download_activity(strava_activity_id)
        logger.info(f"Downloaded {file_ext} ({len(file_bytes)} bytes) "
                     f"for activity {strava_activity_id}")

        # 4. Upload to Garmin Connect
        result = await upload_to_garmin(file_bytes, file_ext)
        garmin_activity_id = extract_garmin_id(result)

        # 5. Log success
        await update_activity_status(
            strava_activity_id, "success",
            garmin_activity_id=garmin_activity_id,
            file_format=file_ext
        )
        logger.info(f"Synced activity {strava_activity_id} → Garmin {garmin_activity_id}")

    except DuplicateActivityError:
        await update_activity_status(strava_activity_id, "duplicate")
        logger.info(f"Activity {strava_activity_id} already exists on Garmin")

    except Exception as e:
        await update_activity_status(
            strava_activity_id, "failed",
            error_message=str(e)
        )
        logger.error(f"Failed to sync activity {strava_activity_id}: {e}")
        # Optionally: queue for retry
```

### Phase 9: Webhook subscription setup script

**Files:** `scripts/setup_webhook.py`

Run once after deployment to register the webhook with Strava:

```python
import httpx

def create_subscription(callback_url: str):
    """
    POST https://www.strava.com/api/v3/push_subscriptions
    Your server must be running and accessible at callback_url.
    """
    resp = httpx.post(
        "https://www.strava.com/api/v3/push_subscriptions",
        data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "callback_url": f"{callback_url}/webhook",
            "verify_token": STRAVA_VERIFY_TOKEN,
        }
    )
    print(resp.json())
    # Returns: {"id": 12345} on success

def list_subscriptions():
    resp = httpx.get(
        "https://www.strava.com/api/v3/push_subscriptions",
        params={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
        }
    )
    print(resp.json())

def delete_subscription(subscription_id: int):
    resp = httpx.delete(
        f"https://www.strava.com/api/v3/push_subscriptions/{subscription_id}",
        params={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
        }
    )
    print(f"Deleted: {resp.status_code}")
```

### Phase 10: Deployment on Render

**Files:** `render.yaml`, `app/services/polling.py`, `app/routers/health.py`

#### Free tier vs paid — what actually happens

The free tier has two limitations, but both have workable solutions:

**1. Cold start vs webhook timeout**

Render free tier spins down after ~15 minutes of inactivity. Cold starts take ~25-30 seconds. Strava requires a 200 response within 2 seconds.

However — Strava retries failed webhook deliveries up to **3 total attempts**. Here's how this plays out:

- **Attempt 1**: Strava POSTs to your sleeping server → Render begins cold start → 25s passes → Strava times out (FAIL)
- **Attempt 2**: Strava retries → your server is now AWAKE (cold start woke it) → responds in <1s → **SUCCESS**

So the webhook will work on the 2nd or 3rd retry in most cases. The cold start from attempt 1 wakes the server, and subsequent retries hit a warm server.

**Risk**: If Strava's retry interval is very short (within the cold start window), all 3 attempts could fail. Strava does not document the exact retry timing. To handle this edge case, we add a **polling fallback**.

**2. Ephemeral filesystem**

Free tier has no persistent disk. SQLite DB and Garmin tokens are wiped on every spin-down/restart/deploy.

**Solution**: Use a free external PostgreSQL database (Render offers free Postgres with 1 GB, expires after 90 days but can be recreated) OR use a free-tier Supabase/Neon Postgres. For Garmin tokens, store the serialized JSON in the database instead of a file.

#### Architecture for free tier

```
┌─── PRIMARY PATH (webhook, works ~90% of the time) ───┐
│ Strava webhook → cold start → retry → process         │
└────────────────────────────────────────────────────────┘
           │ if all 3 retries fail...
           ▼
┌─── FALLBACK PATH (polling, catches the rest) ─────────┐
│ Cron job every 15 min → GET /poll → check Strava API   │
│ for new activities not yet in synced_activities table   │
└────────────────────────────────────────────────────────┘
```

The polling fallback also keeps the server warm, reducing future cold starts.

#### Polling fallback service

```python
# services/polling.py

async def poll_for_new_activities():
    """
    Fallback: check Strava API for recent activities
    that weren't caught by the webhook.
    Called on a schedule or via GET /poll endpoint.
    """
    access_token = await get_valid_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "per_page": 10,
                "after": int((datetime.utcnow() - timedelta(hours=24)).timestamp())
            }
        )
        resp.raise_for_status()
        activities = resp.json()

    for activity in activities:
        activity_id = activity["id"]
        activity_type = activity.get("sport_type", activity.get("type"))

        # Skip if already processed
        if await is_already_synced(activity_id):
            continue

        # Skip if not cycling
        if activity_type not in settings.ACTIVITY_TYPES_TO_SYNC:
            continue

        # Process it (same as webhook path)
        logger.info(f"Polling caught unsynced activity {activity_id}")
        await sync_activity_task(
            strava_activity_id=activity_id,
            owner_id=activity["athlete"]["id"]
        )
```

#### Keep-alive with free external cron

Use **UptimeRobot** (free, up to 50 monitors) or **cron-job.org** (free) to ping your server every 10-14 minutes. This serves double duty:

1. **Prevents spin-down** — keeps the server warm for incoming webhooks
2. **Triggers the polling fallback** — catches any missed webhook events

Setup:
- Create a `GET /poll` endpoint that calls `poll_for_new_activities()`
- Add a secret query param for basic auth: `GET /poll?key=YOUR_SECRET`
- Point UptimeRobot at `https://your-app.onrender.com/poll?key=YOUR_SECRET`
- Set interval to every 10 minutes

```python
# routers/health.py

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/poll")
async def poll_activities(key: str = "", background_tasks: BackgroundTasks):
    """
    Endpoint hit by external cron (UptimeRobot).
    Keeps server alive AND polls for missed activities.
    """
    if key != settings.POLL_SECRET:
        raise HTTPException(403)

    background_tasks.add_task(poll_for_new_activities)
    return {"status": "polling"}
```

#### Database: external PostgreSQL instead of SQLite

Since the filesystem is ephemeral, use an external database. Options (all free):

| Provider | Free tier | Notes |
|---|---|---|
| **Render Postgres** | 1 GB, expires 90 days | Easiest, same platform, recreate when expired |
| **Neon** | 0.5 GB, no expiry | Serverless Postgres, generous free tier |
| **Supabase** | 500 MB, no expiry | Postgres + extras, good free tier |

Update `requirements.txt`:
```
asyncpg>=0.29.0         # async Postgres driver
sqlalchemy[asyncio]>=2.0 # ORM with async support
# Remove: aiosqlite
```

Update `config.py`:
```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Database (external Postgres for free tier)
    DATABASE_URL: str  # e.g. postgresql+asyncpg://user:pass@host/dbname

    # Garmin tokens stored in DB, not filesystem
    # (no GARMIN_TOKEN_DIR needed)

    # Polling
    POLL_SECRET: str  # secret key for /poll endpoint
```

#### Garmin tokens: store in database, not filesystem

Since there's no persistent disk, serialize Garmin tokens to a DB column:

```python
# services/garmin_upload.py

import json
from garminconnect import Garmin

async def get_garmin_client() -> Garmin:
    garmin = Garmin(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD)

    # Try to load saved tokens from database
    token_json = await load_garmin_tokens_from_db()
    if token_json:
        garmin.garth.loads(token_json)
        try:
            garmin.login()  # will use saved tokens, auto-refresh
            # Save refreshed tokens back to DB
            await save_garmin_tokens_to_db(garmin.garth.dumps())
            return garmin
        except Exception:
            pass  # tokens expired, fall through to full login

    # Full login (only needed once, then tokens auto-refresh)
    garmin.login()
    await save_garmin_tokens_to_db(garmin.garth.dumps())
    return garmin
```

#### Render setup (free tier)

1. **Create a Web Service** on Render Dashboard:
   - Connect your GitHub repo
   - Runtime: Python 3
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Instance type: **Free**

2. **Create a PostgreSQL database** on Render (or Neon/Supabase):
   - Copy the connection string to `DATABASE_URL` env var
   - If using Render Postgres: set calendar reminder to recreate before 90-day expiry

3. **Environment variables** (set in Render Dashboard → Environment):
   ```
   STRAVA_CLIENT_ID=...
   STRAVA_CLIENT_SECRET=...
   STRAVA_VERIFY_TOKEN=...
   STRAVA_REFRESH_TOKEN=...
   STRAVA_EMAIL=...
   STRAVA_PASSWORD=...
   GARMIN_EMAIL=...
   GARMIN_PASSWORD=...
   DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
   POLL_SECRET=some-random-secret-string
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```

4. **Set up UptimeRobot** (free):
   - Create account at https://uptimerobot.com
   - Add HTTP(s) monitor: `https://your-app.onrender.com/poll?key=YOUR_POLL_SECRET`
   - Interval: every 10 minutes
   - This keeps the server warm AND triggers polling fallback

5. **Infrastructure-as-code** (optional `render.yaml`):
   ```yaml
   services:
     - type: web
       name: strava-to-garmin
       runtime: python
       buildCommand: pip install -r requirements.txt
       startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
       plan: free
       envVars:
         - key: PYTHON_VERSION
           value: 3.12.0
   databases:
     - name: strava-garmin-db
       plan: free
       databaseName: strava_garmin
   ```

#### Paid tier alternative ($7/month)

If you'd rather skip the complexity of polling fallback + external DB + UptimeRobot, the Starter plan at $7/month gives you:
- Always-on (no cold starts, webhooks always work on first attempt)
- Persistent disk (use simple SQLite, store Garmin tokens on filesystem)
- No need for polling fallback or UptimeRobot

Use `plan: starter` in render.yaml and add a disk mount at `/var/data`.

#### Render-specific gotchas (both tiers)

- **HTTPS is automatic** — Render provides free TLS on `*.onrender.com` domains. No cert setup needed. Your webhook URL will be `https://your-app.onrender.com/webhook`.
- **PORT is dynamic** — Render sets the `$PORT` env var. Always bind to `0.0.0.0:$PORT`, never hardcode 8000.
- **Health checks** — Render pings your `/health` endpoint to determine if the service is alive. If it fails, Render restarts the service. Make sure `/health` returns 200 quickly.
- **Deploy triggers** — every `git push` to your connected branch triggers a redeploy.
- **Garmin initial auth** — first login may require MFA. Run `scripts/setup_garmin_auth.py` locally, then store the resulting tokens in the database via a one-time script.
- **Log access** — use Render Dashboard → Logs to monitor webhook events and sync status.

### Phase 11: Telegram notifications

**Files:** `services/notifications.py`, update to `config.py`

Telegram is by far the easiest notification option — it requires zero infrastructure, no email server, no SMTP credentials, and works with a single HTTP request. No library needed beyond `httpx` (which you already have).

#### One-time Telegram setup (5 minutes)

1. **Create a bot**: Open Telegram, search for `@BotFather`, send `/newbot`, pick a name and username. BotFather gives you a **bot token** (long string like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`).

2. **Get your chat ID**: 
   - Start a conversation with your new bot (search for it and click Start)
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   - Find `"chat":{"id": 123456789}` in the response — that number is your chat ID

3. **Add to `.env`**:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
   TELEGRAM_CHAT_ID=123456789
   ```

#### Config additions

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Telegram notifications (optional — leave blank to disable)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    @property
    def notifications_enabled(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)
```

#### Notification service

```python
# services/notifications.py
import httpx
from app.config import settings
import logging

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

async def send_telegram(message: str) -> bool:
    """
    Send a message via Telegram Bot API.
    This is a single HTTP POST — no library needed.
    Returns True if sent successfully, False otherwise.
    """
    if not settings.notifications_enabled:
        return False

    url = TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")
        return False


# Convenience helpers with pre-formatted messages

async def notify_sync_success(
    activity_name: str,
    activity_type: str,
    strava_id: int,
    garmin_id: str | None,
    file_format: str,
):
    msg = (
        f"✅ <b>Activity synced!</b>\n\n"
        f"📋 <b>{activity_name}</b>\n"
        f"🚴 Type: {activity_type}\n"
        f"📁 Format: {file_format}\n"
        f"🔗 <a href='https://www.strava.com/activities/{strava_id}'>Strava</a>"
    )
    if garmin_id:
        msg += (
            f" → <a href='https://connect.garmin.com/"
            f"modern/activity/{garmin_id}'>Garmin</a>"
        )
    await send_telegram(msg)


async def notify_sync_failure(
    strava_id: int,
    error: str,
):
    msg = (
        f"❌ <b>Sync failed</b>\n\n"
        f"Activity: <a href='https://www.strava.com/"
        f"activities/{strava_id}'>{strava_id}</a>\n"
        f"Error: <code>{error[:200]}</code>"
    )
    await send_telegram(msg)


async def notify_skipped(
    strava_id: int,
    reason: str,
):
    msg = f"⏭️ Skipped activity {strava_id}: {reason}"
    await send_telegram(msg)
```

#### Integration into sync task

Add notifications to `tasks/sync_activity.py`:

```python
from app.services.notifications import (
    notify_sync_success, notify_sync_failure, notify_skipped
)

async def sync_activity_task(strava_activity_id: int, owner_id: int):
    try:
        # ... existing steps 1-2 (record + check type) ...

        if activity_type not in settings.ACTIVITY_TYPES_TO_SYNC:
            await update_activity_status(strava_activity_id, "skipped")
            await notify_skipped(strava_activity_id, f"Type: {activity_type}")
            return

        # ... existing steps 3-4 (download + upload) ...

        # Step 5: notify success
        await notify_sync_success(
            activity_name=activity_name,
            activity_type=activity_type,
            strava_id=strava_activity_id,
            garmin_id=garmin_activity_id,
            file_format=file_ext,
        )

    except DuplicateActivityError:
        await update_activity_status(strava_activity_id, "duplicate")
        await notify_skipped(strava_activity_id, "Already on Garmin")

    except Exception as e:
        await update_activity_status(strava_activity_id, "failed", error_message=str(e))
        await notify_sync_failure(strava_activity_id, str(e))
```

#### Why Telegram over email

| | Telegram | Email (SMTP) |
|---|---|---|
| Setup time | ~5 minutes | ~30 minutes |
| Dependencies | None (just `httpx`) | `aiosmtplib` + email templates |
| Infrastructure | None | SMTP server (Gmail, SendGrid, etc.) |
| Auth complexity | One bot token | SMTP host, port, TLS, app password |
| Delivery speed | Instant | 1-30 seconds |
| Rich formatting | HTML, links, emoji | Full HTML but more work |
| Mobile push | Built-in | Depends on email app |
| Failure modes | Rare | SPF/DKIM/spam filters, rate limits |
| Code required | ~15 lines | ~40 lines + templates |
| Cost | Free forever | Free tier limits on most providers |

Telegram is the clear winner for personal-use notification. The entire implementation is one HTTP POST call. Email is only worth it if you need to notify people who don't use Telegram.

---

## Key API details

### Strava webhook payload (POST to your /webhook)

```json
{
    "aspect_type": "create",
    "event_time": 1725991232,
    "object_id": 12345678,
    "object_type": "activity",
    "owner_id": 67890,
    "subscription_id": 98765,
    "updates": {}
}
```

- `object_type`: "activity" or "athlete"
- `aspect_type`: "create", "update", or "delete"
- `object_id`: the Strava activity ID
- `owner_id`: the athlete ID who owns the activity
- The payload does NOT contain activity data — you must fetch it separately via the API.

### Strava activity detail (GET)

```
GET https://www.strava.com/api/v3/activities/{id}
Authorization: Bearer {access_token}
```

Key fields to use: `type`, `sport_type`, `name`, `start_date`, `distance`, `moving_time`.

### Strava export original (web scrape, NOT official API)

```
GET https://www.strava.com/activities/{id}/export_original
Cookie: _strava4_session=...
```

Returns the original uploaded file (.fit, .tcx, .gpx, or .json). Requires authenticated web session cookies.

### Strava Streams API (official)

```
GET https://www.strava.com/api/v3/activities/{id}/streams
?keys=time,latlng,altitude,heartrate,cadence,watts,temp
&key_type=time
Authorization: Bearer {access_token}
```

Returns arrays of data points. Use `strava2gpx` to compile into GPX.

### Garmin Connect upload

Using `python-garminconnect`:

```python
garmin.import_activity("/path/to/file.fit")
# Returns DetailedImportResult with successes, failures, activity IDs
# HTTP 409 if activity already exists (duplicate detection)
```

---

## Critical gotchas and edge cases

1. **2-second webhook timeout**: Strava expects a 200 response within 2 seconds. All processing must happen in a background task, never in the request handler.

2. **Fake webhook protection**: Anyone can POST to your webhook endpoint if they know the URL and your app's subscription ID. Validate `subscription_id` in incoming events. Optionally, after receiving an event, verify the activity exists by calling the Strava API before processing.

3. **Strava token refresh**: Access tokens expire after 6 hours. Always check `expires_at` before making API calls and refresh proactively.

4. **Garmin MFA**: On first login, if the Garmin account has MFA enabled, the `garminconnect` library will prompt for a code. Run `scripts/setup_garmin_auth.py` interactively first. After initial auth, tokens auto-refresh without MFA.

5. **Garmin re-export loop prevention**: Use `import_activity()` (not `upload_activity()`). The import endpoint uses Garmin Connect Mobile headers, so Garmin treats it as an import and does NOT re-export to connected third-party services like Strava.

6. **Duplicate detection**: Before downloading, check `synced_activities` table. After upload, handle 409 responses gracefully.

7. **Web scrape fragility**: The `export_original` URL is not an official API. Strava could change their web auth flow at any time. The Streams API → GPX fallback ensures the app keeps working.

8. **Strava web session cookies**: The web scrape needs a logged-in browser session. Use `httpx` to POST to `https://www.strava.com/session` with email/password. Store cookies and refresh when they expire. Watch for CAPTCHA or rate limiting.

9. **Rate limits**: Strava API has a 100 requests per 15 minutes and 1000 per day limit for read operations. For a personal-use app syncing your own rides, this is plenty. If supporting multiple users, implement queuing.

10. **Activity types**: Strava has both `type` and `sport_type` fields. `sport_type` is more granular (e.g., `MountainBikeRide` vs `GravelRide`). Filter on `type` which groups them (all cycling types have `type: "Ride"`), or use `sport_type` for finer control.

---

## Setup checklist (first-time)

1. **Create a Strava API application** at https://www.strava.com/settings/api
   - Set Authorization Callback Domain to your `*.onrender.com` domain
   - Note your `client_id` and `client_secret`

2. **Create a Garmin Connect account** if you don't have one at https://connect.garmin.com

3. **Create a Telegram bot** (optional but recommended):
   - Message `@BotFather` on Telegram → `/newbot` → save the bot token
   - Start a chat with your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to get your chat ID

4. **Run setup scripts locally** (before deploying):
   ```bash
   python scripts/setup_strava_auth.py    # Get Strava refresh token
   python scripts/setup_garmin_auth.py    # Authenticate with Garmin, saves token file
   ```

5. **Deploy to Render**:
   - Create a Web Service (Free or Starter plan), connect your GitHub repo
   - Create a PostgreSQL database (free tier) OR attach persistent disk (paid tier)
   - Add all environment variables in Render Dashboard

6. **Store Garmin tokens**:
   - Free tier: run a one-time script to serialize tokens into the database
   - Paid tier: SCP/SSH `garmin_tokens.json` to `/var/data/garmin_tokens/`

7. **Set up UptimeRobot** (free tier only):
   - Create a free monitor at https://uptimerobot.com
   - Point it at `https://your-app.onrender.com/poll?key=YOUR_SECRET` every 10 minutes

8. **Register the webhook** (after service is live):
   ```bash
   python scripts/setup_webhook.py --create --url https://your-app.onrender.com
   ```

9. **Test** by uploading a cycling activity to Strava and checking Render logs + Telegram notifications

---

## Optional enhancements (future)

- **Retry queue**: Failed uploads get retried with exponential backoff (use Celery + Redis, or Render's Background Workers)
- **Web dashboard**: Simple HTML page showing sync history, last sync time, error log
- **Multi-user support**: Add user registration flow, store per-user Strava + Garmin tokens
- **Activity name sync**: After Garmin upload, update the Garmin activity name to match the Strava title
- **Bidirectional sync**: Also sync Garmin → Strava (different webhook, same architecture)
- **FIT file construction**: Instead of GPX fallback, build proper .FIT files from Strava Streams using `fit-tool` Python library for richer data preservation
- **Email notifications**: If Telegram isn't suitable, add `aiosmtplib` for email via Gmail/SendGrid (see comparison table in Phase 11)