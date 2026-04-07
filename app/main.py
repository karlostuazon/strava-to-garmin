import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import init_db
from app.routers import auth, health, webhook


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Strava to Garmin Sync",
    description="Auto-sync cycling activities from Strava to Garmin Connect",
    lifespan=lifespan,
)

app.include_router(webhook.router)
app.include_router(auth.router)
app.include_router(health.router)
