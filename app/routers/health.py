from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.config import settings
from app.services.polling import poll_for_new_activities

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/poll")
async def poll_activities(key: str = "", background_tasks: BackgroundTasks = None):
    if key != settings.POLL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid poll secret")

    background_tasks.add_task(poll_for_new_activities)
    return {"status": "polling"}
