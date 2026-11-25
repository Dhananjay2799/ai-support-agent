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
    # Abuse protection for the public demo (see app/limits.py). Counters live in Postgres
    # because serverless instances don't share memory.
    rate_limits_enabled: bool = True
    chat_per_session_per_minute: int = 20
    chat_per_ip_per_hour: int = 150
    voice_calls_per_ip_per_hour: int = 8
    resets_per_ip_per_hour: int = 12
    daily_budget_usd: float = Field(
        default=5.0, description="Chat and voice pause once today's spend reaches this"
    )
    ip_hash_salt: str = Field(default="acme-demo", description="Client IPs are only stored as salted hashes")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
