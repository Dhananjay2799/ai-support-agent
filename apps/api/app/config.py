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
    # Model ids are configuration, not code: OpenAI's lineup changes often.
    agent_model: str = "gpt-6-luna"
    judge_model: str = "gpt-6-luna"
    # Used when the agent model times out or is rate-limited on every retry.
    agent_fallback_model: str | None = "gpt-5.4-mini"
    agent_reasoning_effort: Literal["none", "low", "medium", "high"] | None = "low"
    # The judge answers a yes/no question; low effort keeps it around 1-2 s.
    judge_reasoning_effort: Literal["none", "low", "medium", "high"] | None = "low"
    demo_mode: bool = Field(default=True, description="Enables personas and the chaos panel")
    admin_token: str | None = Field(default=None, description="Enables /admin endpoints when set")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
