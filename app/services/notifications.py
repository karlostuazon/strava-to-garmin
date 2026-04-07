import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(message: str) -> bool:
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


async def notify_sync_starting(strava_id: int, activity_name: str = ""):
    name_part = f" — <b>{activity_name}</b>" if activity_name else ""
    msg = f"🔄 <b>Sync starting</b>{name_part}\n\nActivity: <a href='https://www.strava.com/activities/{strava_id}'>{strava_id}</a>"
    await send_telegram(msg)


async def notify_download_in_progress(strava_id: int):
    msg = f"⬇️ <b>Downloading</b> activity {strava_id} from Strava..."
    await send_telegram(msg)


async def notify_upload_in_progress(strava_id: int, file_format: str):
    msg = f"⬆️ <b>Uploading</b> {file_format} to Garmin Connect for activity {strava_id}..."
    await send_telegram(msg)


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
        msg += f" → <a href='https://connect.garmin.com/modern/activity/{garmin_id}'>Garmin</a>"
    await send_telegram(msg)


async def notify_sync_failure(strava_id: int, error: str):
    msg = (
        f"❌ <b>Sync failed</b>\n\n"
        f"Activity: <a href='https://www.strava.com/activities/{strava_id}'>{strava_id}</a>\n"
        f"Error: <code>{error[:200]}</code>"
    )
    await send_telegram(msg)


async def notify_skipped(strava_id: int, reason: str):
    msg = f"⏭️ Skipped activity {strava_id}: {reason}"
    await send_telegram(msg)
