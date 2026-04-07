import logging

from fastapi import APIRouter, HTTPException, Request

from app.database import async_session, StravaToken
from app.services.strava_auth import exchange_code_for_tokens

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")


@router.get("/strava")
async def strava_oauth_callback(request: Request):
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    token_data = await exchange_code_for_tokens(code)

    async with async_session() as session:
        token = StravaToken(
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=token_data["expires_at"],
        )
        session.add(token)
        await session.commit()

    logger.info("Strava OAuth tokens saved")
    return {"status": "success", "athlete": token_data.get("athlete", {}).get("firstname", "Unknown")}
