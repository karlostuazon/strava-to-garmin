#!/usr/bin/env python3
"""One-time script to walk through Strava OAuth flow and get refresh token."""

import webbrowser

from app.config import settings

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"


def main():
    auth_url = (
        f"{AUTHORIZE_URL}"
        f"?client_id={settings.STRAVA_CLIENT_ID}"
        f"&redirect_uri=http://localhost:8000/auth/strava"
        f"&response_type=code"
        f"&scope=activity:read_all"
        f"&approval_prompt=auto"
    )

    print("Opening browser for Strava authorization...")
    print(f"\nURL: {auth_url}\n")
    print("After authorizing, Strava will redirect to http://localhost:8000/auth/strava")
    print("Make sure the FastAPI server is running: uvicorn app.main:app")

    webbrowser.open(auth_url)


if __name__ == "__main__":
    main()
