"""
Application settings loaded from environment variables via pydantic-settings.
All required variables are validated at startup; missing ones raise a clear error.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Required ───────────────────────────────────────────────────────────────
    database_url: str
    groq_api_key: str
    deepgram_api_key: str
    github_client_id: str
    github_client_secret: str

    # ── Optional with sensible defaults ────────────────────────────────────────
    groq_model: str = "openai/gpt-oss-120b"
    frontend_url: str = "https://frontend-bice-one-8o0ryl9h02.vercel.app"
    backend_url: str = "http://localhost:8000"
    port: int = 8000
    app_env: str = "development"

    # ── Payments (optional — leave blank to disable) ───────────────────────────
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
