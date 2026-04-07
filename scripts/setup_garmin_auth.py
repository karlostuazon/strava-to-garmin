#!/usr/bin/env python3
"""One-time script to authenticate with Garmin Connect and save tokens."""

import asyncio

from garminconnect import Garmin

from app.config import settings
from app.database import init_db
from app.services.garmin_upload import _save_garmin_tokens_to_db


async def main():
    await init_db()

    print(f"Logging into Garmin Connect as {settings.GARMIN_EMAIL}...")
    garmin = Garmin(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD)
    garmin.login()

    token_json = garmin.garth.dumps()
    await _save_garmin_tokens_to_db(token_json)

    print("Garmin auth successful. Tokens saved to database.")


if __name__ == "__main__":
    asyncio.run(main())
