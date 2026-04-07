import logging
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.services.strava_auth import get_valid_access_token

logger = logging.getLogger(__name__)

STRAVA_SESSION_URL = "https://www.strava.com/session"
STRAVA_LOGIN_URL = "https://www.strava.com/login"

_web_cookies: dict | None = None


async def _get_strava_web_session(client: httpx.AsyncClient) -> None:
    global _web_cookies

    if _web_cookies:
        client.cookies.update(_web_cookies)
        return

    # Get CSRF token from login page
    login_resp = await client.get(STRAVA_LOGIN_URL)
    soup = BeautifulSoup(login_resp.text, "html.parser")
    csrf_token = ""
    csrf_input = soup.find("input", {"name": "authenticity_token"})
    if csrf_input:
        csrf_token = csrf_input.get("value", "")

    # Login with email/password
    resp = await client.post(
        STRAVA_SESSION_URL,
        data={
            "email": settings.STRAVA_EMAIL,
            "password": settings.STRAVA_PASSWORD,
            "authenticity_token": csrf_token,
        },
        follow_redirects=True,
    )

    if resp.status_code != 200 or "login" in str(resp.url):
        raise RuntimeError("Strava web login failed — check STRAVA_EMAIL/STRAVA_PASSWORD")

    _web_cookies = dict(client.cookies)
    logger.info("Strava web session established")


async def download_original_file(activity_id: int) -> tuple[bytes, str]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        await _get_strava_web_session(client)

        url = f"https://www.strava.com/activities/{activity_id}/export_original"
        response = await client.get(url)

        # If we got HTML back, session is invalid — re-login and retry
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            global _web_cookies
            _web_cookies = None
            await _get_strava_web_session(client)
            response = await client.get(url)

        response.raise_for_status()

        # Determine file format from Content-Disposition header
        content_disp = response.headers.get("content-disposition", "")
        extension = ".fit"  # default
        if "filename=" in content_disp:
            filename = content_disp.split("filename=")[-1].strip('"').strip("'")
            extension = Path(filename).suffix.lower()

        return response.content, extension


async def download_as_gpx(activity_id: int) -> bytes:
    from strava2gpx import strava2gpx

    refresh_token = await _get_refresh_token_for_strava2gpx()

    s2g = strava2gpx(
        settings.STRAVA_CLIENT_ID,
        settings.STRAVA_CLIENT_SECRET,
        refresh_token,
    )
    await s2g.connect()

    tmp_path = f"/tmp/strava_{activity_id}"
    await s2g.write_to_gpx(activity_id, tmp_path)

    gpx_path = f"{tmp_path}.gpx"
    with open(gpx_path, "rb") as f:
        return f.read()


async def _get_refresh_token_for_strava2gpx() -> str:
    from sqlalchemy import select
    from app.database import async_session, StravaToken

    async with async_session() as session:
        result = await session.execute(select(StravaToken).order_by(StravaToken.id.desc()).limit(1))
        token = result.scalar_one_or_none()
        return token.refresh_token if token else settings.STRAVA_REFRESH_TOKEN


async def download_activity(activity_id: int) -> tuple[bytes, str]:
    try:
        file_bytes, ext = await download_original_file(activity_id)
        if ext in (".fit", ".tcx", ".gpx"):
            logger.info(f"Downloaded original {ext} for activity {activity_id}")
            return file_bytes, ext
        # Original is .json (Strava mobile) — not useful, fall through
        logger.info(f"Original format is {ext}, falling back to GPX")
    except Exception as e:
        logger.warning(f"Web scrape failed for {activity_id}: {e}")

    # Fallback: build GPX from Streams API
    logger.info(f"Falling back to Streams API for activity {activity_id}")
    gpx_bytes = await download_as_gpx(activity_id)
    return gpx_bytes, ".gpx"
