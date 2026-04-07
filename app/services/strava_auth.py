import logging
import time

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session, StravaToken

logger = logging.getLogger(__name__)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


async def get_valid_access_token() -> str:
    async with async_session() as session:
        result = await session.execute(select(StravaToken).order_by(StravaToken.id.desc()).limit(1))
        token = result.scalar_one_or_none()

        if token and token.expires_at > time.time() + 60:
            return token.access_token

        refresh_token = token.refresh_token if token else settings.STRAVA_REFRESH_TOKEN
        new_token_data = await _refresh_token(refresh_token)

        if token:
            token.access_token = new_token_data["access_token"]
            token.refresh_token = new_token_data["refresh_token"]
            token.expires_at = new_token_data["expires_at"]
        else:
            token = StravaToken(
                access_token=new_token_data["access_token"],
                refresh_token=new_token_data["refresh_token"],
                expires_at=new_token_data["expires_at"],
            )
            session.add(token)

        await session.commit()
        logger.info("Strava access token refreshed")
        return new_token_data["access_token"]


async def exchange_code_for_tokens(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _refresh_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return resp.json()
