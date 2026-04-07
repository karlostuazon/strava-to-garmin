# Strava to Garmin Connect Auto-Sync

A Python backend service that listens for new cycling activities uploaded to Strava via webhooks, downloads the original activity file (preferring .FIT format), and automatically uploads it to Garmin Connect.

## Features

- **Webhook-driven**: Automatically triggered when you upload a ride to Strava
- **FIT file preferred**: Downloads original .FIT files via web scrape, falls back to GPX via Streams API
- **No sync loops**: Uses Garmin's import endpoint so activities won't re-export back to Strava
- **Polling fallback**: Catches missed webhook events on free-tier hosting
- **Telegram notifications**: Real-time updates on sync status at every stage
- **Cycling only**: Filters for Ride, VirtualRide, EBikeRide (configurable)

## Tech Stack

- **FastAPI** — async web framework
- **SQLAlchemy + asyncpg** — async PostgreSQL
- **python-garminconnect** — Garmin Connect upload via import endpoint
- **strava2gpx** — GPX fallback from Strava Streams API
- **httpx** — async HTTP client for web scraping and API calls
- **pydantic-settings** — configuration from `.env`

## Setup

1. Copy `.env.example` to `.env` and fill in your credentials
2. Run setup scripts:
   ```bash
   python scripts/setup_strava_auth.py   # Get Strava refresh token
   python scripts/setup_garmin_auth.py   # Authenticate with Garmin
   ```
3. Deploy to Render (or run locally):
   ```bash
   pip install -r requirements.txt
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
4. Register the Strava webhook:
   ```bash
   python scripts/setup_webhook.py --create --url https://your-app.onrender.com
   ```

## Deployment

Designed for Render free tier with external PostgreSQL. See `render.yaml` for infrastructure-as-code config.
