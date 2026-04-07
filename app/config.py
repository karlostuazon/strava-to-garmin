from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Strava API
    STRAVA_CLIENT_ID: str
    STRAVA_CLIENT_SECRET: str
    STRAVA_VERIFY_TOKEN: str
    STRAVA_REFRESH_TOKEN: str

    # Strava web session (for FIT download scraping)
    STRAVA_EMAIL: str
    STRAVA_PASSWORD: str

    # Garmin Connect
    GARMIN_EMAIL: str
    GARMIN_PASSWORD: str

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://localhost/strava_garmin"

    # Polling
    POLL_SECRET: str = ""

    # Telegram notifications (optional)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # App
    LOG_LEVEL: str = "INFO"
    ACTIVITY_TYPES_TO_SYNC: list[str] = ["Ride", "VirtualRide", "EBikeRide"]

    @property
    def notifications_enabled(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
