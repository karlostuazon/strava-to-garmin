import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.database import async_session, SyncedActivity
from app.services.strava_auth import get_valid_access_token
from app.tasks.sync_activity import sync_activity_task
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def poll_for_new_activities():
    access_token = await get_valid_access_token()
    after_ts = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": 10, "after": after_ts},
        )
        resp.raise_for_status()
        activities = resp.json()

    for activity in activities:
        activity_id = activity["id"]
        activity_type = activity.get("sport_type", activity.get("type"))

        # Skip if already processed
        async with async_session() as session:
            result = await session.execute(
                select(SyncedActivity).where(SyncedActivity.strava_activity_id == activity_id)
            )
            if result.scalar_one_or_none():
                continue

        # Skip if not cycling
        if activity_type not in settings.ACTIVITY_TYPES_TO_SYNC:
            continue

        logger.info(f"Polling caught unsynced activity {activity_id}")
        await sync_activity_task(
            strava_activity_id=activity_id,
            owner_id=activity["athlete"]["id"],
        )
