from functools import lru_cache

from openai import AsyncOpenAI

from app.config import get_settings


@lru_cache
def get_openai() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    # Short timeouts with SDK retries: a hung upstream call should fail fast and
    # surface as a recoverable error in the UI, not freeze the turn.
    return AsyncOpenAI(api_key=settings.openai_api_key, timeout=20.0, max_retries=2)
