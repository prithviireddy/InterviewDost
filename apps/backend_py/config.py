"""
Application settings loaded from environment variables via pydantic-settings.
All required variables are validated at startup; missing ones raise a clear error.
"""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    #  Required 
    database_url: str
    groq_api_key: str
    deepgram_api_key: str
    google_client_id: str = ""
    google_client_secret: str = ""

    #  Optional with sensible defaults 
    groq_model: str = "openai/gpt-oss-120b"
    frontend_url: str = "https://interview-dost-wine.vercel.app"
    backend_url: str = "http://localhost:8000"
    port: int = 8000
    app_env: str = "development"

    #  Payments (leave blank to disable) 
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    @field_validator("frontend_url", "backend_url", mode="before")
    @classmethod
    def clean_url(cls, v: str) -> str:
        """Strip whitespace, carriage returns, newlines, and trailing slashes."""
        if not v:
            return ""
        return str(v).strip().replace("\r", "").replace("\n", "").rstrip("/")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
