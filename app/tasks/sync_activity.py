import logging

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session, SyncedActivity
from app.services.garmin_upload import DuplicateActivityError, extract_garmin_id, upload_to_garmin
from app.services.notifications import (
    notify_download_in_progress,
    notify_skipped,
    notify_sync_failure,
    notify_sync_starting,
    notify_sync_success,
    notify_upload_in_progress,
)
from app.services.strava_auth import get_valid_access_token
from app.services.strava_download import download_activity

logger = logging.getLogger(__name__)


async def get_activity_details(activity_id: int) -> dict:
    access_token = await get_valid_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def create_activity_record(strava_activity_id: int, status: str = "pending") -> None:
    async with async_session() as session:
        record = SyncedActivity(strava_activity_id=strava_activity_id, status=status)
        session.add(record)
        await session.commit()


async def update_activity_status(
    strava_activity_id: int,
    status: str,
    *,
    activity_name: str = "",
    activity_type: str = "",
    garmin_activity_id: str = "",
    file_format: str = "",
    error_message: str = "",
) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(SyncedActivity).where(SyncedActivity.strava_activity_id == strava_activity_id)
        )
        record = result.scalar_one_or_none()
        if record:
            record.status = status
            if activity_name:
                record.activity_name = activity_name
            if activity_type:
                record.activity_type = activity_type
            if garmin_activity_id:
                record.garmin_activity_id = garmin_activity_id
            if file_format:
                record.file_format = file_format
            if error_message:
                record.error_message = error_message
            await session.commit()


async def sync_activity_task(strava_activity_id: int, owner_id: int) -> None:
    try:
        # 1. Record as pending
        await create_activity_record(strava_activity_id, status="pending")

        # 2. Fetch activity details and check type
        details = await get_activity_details(strava_activity_id)
        activity_type = details.get("sport_type", details.get("type", "Unknown"))
        activity_name = details.get("name", "Untitled")

        await notify_sync_starting(strava_activity_id, activity_name)

        if activity_type not in settings.ACTIVITY_TYPES_TO_SYNC:
            await update_activity_status(
                strava_activity_id, "skipped",
                activity_name=activity_name, activity_type=activity_type,
            )
            await notify_skipped(strava_activity_id, f"Type: {activity_type}")
            return

        # 3. Download activity file
        await notify_download_in_progress(strava_activity_id)
        file_bytes, file_ext = await download_activity(strava_activity_id)
        logger.info(f"Downloaded {file_ext} ({len(file_bytes)} bytes) for activity {strava_activity_id}")

        # 4. Upload to Garmin Connect
        await notify_upload_in_progress(strava_activity_id, file_ext)
        result = await upload_to_garmin(file_bytes, file_ext)
        garmin_activity_id = extract_garmin_id(result)

        # 5. Log success and notify
        await update_activity_status(
            strava_activity_id, "success",
            activity_name=activity_name,
            activity_type=activity_type,
            garmin_activity_id=garmin_activity_id or "",
            file_format=file_ext,
        )
        logger.info(f"Synced activity {strava_activity_id} → Garmin {garmin_activity_id}")
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
        logger.info(f"Activity {strava_activity_id} already exists on Garmin")

    except Exception as e:
        await update_activity_status(strava_activity_id, "failed", error_message=str(e))
        await notify_sync_failure(strava_activity_id, str(e))
        logger.error(f"Failed to sync activity {strava_activity_id}: {e}")
