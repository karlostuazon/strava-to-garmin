import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select

from app.config import settings
from app.database import async_session, SyncedActivity
from app.models import StravaWebhookEvent
from app.tasks.sync_activity import sync_activity_task

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == settings.STRAVA_VERIFY_TOKEN:
        logger.info("Strava webhook subscription validated")
        return {"hub.challenge": challenge}
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(event: StravaWebhookEvent, background_tasks: BackgroundTasks):
    # Only process activity creates
    if event.object_type != "activity" or event.aspect_type != "create":
        return {"status": "ignored"}

    # Idempotency check
    async with async_session() as session:
        result = await session.execute(
            select(SyncedActivity).where(
                SyncedActivity.strava_activity_id == event.object_id
            )
        )
        if result.scalar_one_or_none():
            return {"status": "already_synced"}

    # Queue background task
    background_tasks.add_task(
        sync_activity_task,
        strava_activity_id=event.object_id,
        owner_id=event.owner_id,
    )
    return {"status": "processing"}
