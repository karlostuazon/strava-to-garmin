import json
import logging
from pathlib import Path

from garminconnect import Garmin
from sqlalchemy import select

from app.config import settings
from app.database import async_session, GarminToken

logger = logging.getLogger(__name__)


class DuplicateActivityError(Exception):
    pass


async def _load_garmin_tokens_from_db() -> str | None:
    async with async_session() as session:
        result = await session.execute(select(GarminToken).order_by(GarminToken.id.desc()).limit(1))
        token = result.scalar_one_or_none()
        return token.token_json if token else None


async def _save_garmin_tokens_to_db(token_json: str) -> None:
    async with async_session() as session:
        result = await session.execute(select(GarminToken).order_by(GarminToken.id.desc()).limit(1))
        token = result.scalar_one_or_none()
        if token:
            token.token_json = token_json
        else:
            session.add(GarminToken(token_json=token_json))
        await session.commit()


async def get_garmin_client() -> Garmin:
    garmin = Garmin(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD)

    token_json = await _load_garmin_tokens_from_db()
    if token_json:
        garmin.garth.loads(token_json)
        try:
            garmin.login()
            await _save_garmin_tokens_to_db(garmin.garth.dumps())
            return garmin
        except Exception:
            logger.warning("Saved Garmin tokens expired, performing full login")

    garmin.login()
    await _save_garmin_tokens_to_db(garmin.garth.dumps())
    return garmin


async def upload_to_garmin(file_bytes: bytes, file_extension: str) -> dict:
    client = await get_garmin_client()

    tmp_path = f"/tmp/garmin_upload{file_extension}"
    with open(tmp_path, "wb") as f:
        f.write(file_bytes)

    try:
        result = client.import_activity(tmp_path)
        return result
    except Exception as e:
        error_str = str(e)
        if "409" in error_str or "duplicate" in error_str.lower():
            raise DuplicateActivityError(f"Activity already exists on Garmin: {e}")
        # Auth expired — re-login and retry once
        if "401" in error_str or "expired" in error_str.lower():
            logger.warning("Garmin auth expired, re-authenticating")
            client = await get_garmin_client()
            result = client.import_activity(tmp_path)
            return result
        raise
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def extract_garmin_id(result: dict) -> str | None:
    try:
        successes = result.get("detailedImportResult", {}).get("successes", [])
        if successes:
            return str(successes[0].get("internalId", ""))
    except (AttributeError, IndexError, KeyError):
        pass
    return None
