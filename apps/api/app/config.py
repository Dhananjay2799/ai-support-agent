from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "production", "test"] = "local"
    # Comma-separated list, e.g. "http://localhost:3000,https://ai-support-agent.vercel.app"
    cors_origins: str = "http://localhost:3000"
    database_url: str | None = Field(default=None, description="Postgres connection string")
    openai_api_key: str | None = None
    demo_mode: bool = Field(default=True, description="Enables personas and the chaos panel")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
