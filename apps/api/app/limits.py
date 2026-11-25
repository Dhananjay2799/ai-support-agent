"""Abuse protection for the public demo: request limits and a daily spend cap.

Limits are fixed-window counters in Postgres (`public.hit_rate_limit`), since each
serverless instance has its own memory. They are checked in a dependency, before a
streaming response starts, so a refused request gets a real 429 or 503 with
`Retry-After` instead of a half-open stream. Client IPs are only stored as salted hashes.

If the database is unreachable the limiter lets requests through: the request will
fail on its own, and a broken limiter shouldn't take the demo down with it.
"""

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException, Request

from app.config import Settings, get_settings
from app.db import get_pool

log = logging.getLogger(__name__)

Route = Literal["chat", "voice", "reset"]


@dataclass(frozen=True)
class Limit:
    bucket: str  # e.g. "chat:session:<id>"
    max_hits: int
    window_s: int
    message: str


def client_ip(request: Request) -> str:
    """The caller's IP as Vercel reports it, falling back to the socket peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return (
        request.headers.get("x-real-ip")
        or forwarded.split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


def hashed(value: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:24]


def limits_for(route: Route, ip_hash: str, session_id: str | None, settings: Settings) -> list[Limit]:
    if route == "chat":
        limits = [
            Limit(
                f"chat:ip:{ip_hash}",
                settings.chat_per_ip_per_hour,
                3600,
                "Too many messages from your network.",
            ),
        ]
        if session_id:
            limits.insert(
                0,
                Limit(
                    f"chat:session:{session_id}",
                    settings.chat_per_session_per_minute,
                    60,
                    "Too many messages in this conversation.",
                ),
            )
        return limits
    if route == "voice":
        return [Limit(f"voice:ip:{ip_hash}", settings.voice_calls_per_ip_per_hour, 3600, "Too many calls.")]
    return [Limit(f"reset:ip:{ip_hash}", settings.resets_per_ip_per_hour, 3600, "Too many resets.")]


def _retry_after(window_s: int) -> int:
    return max(1, window_s - int(time.time()) % window_s)


def _seconds_to_midnight_utc() -> int:
    now = datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight - now).total_seconds()) + 1


async def _session_id(request: Request) -> str | None:
    """The conversation id from the JSON body (Starlette caches the body for the route)."""
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        return None
    value = body.get("session_id") if isinstance(body, dict) else None
    return str(value)[:64] if value else None


async def hit(bucket: str, window_s: int) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute("select public.hit_rate_limit(%s, %s) as hits", (bucket, window_s))
        row = await cur.fetchone()
    return int(row["hits"]) if row else 0


async def spent_today() -> float:
    """Today's spend (UTC): agent turns and voice calls each store one root span with its cost."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select coalesce(sum((attributes ->> 'cost_usd')::numeric), 0) as spent
            from public.traces
            where parent_span_id is null and created_at >= date_trunc('day', now() at time zone 'utc')
            """
        )
        row = await cur.fetchone()
    return float(row["spent"]) if row else 0.0


def limited(route: Route) -> Callable[[Request], Awaitable[None]]:
    """A FastAPI dependency that refuses the request when a limit or the budget is hit."""

    async def check(request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limits_enabled:
            return
        try:
            if route != "reset" and settings.daily_budget_usd > 0:
                spent = await spent_today()
                if spent >= settings.daily_budget_usd:
                    raise HTTPException(
                        status_code=503,
                        detail="The demo has used today's budget. It resets at midnight UTC.",
                        headers={"Retry-After": str(_seconds_to_midnight_utc())},
                    )
            ip_hash = hashed(client_ip(request), settings.ip_hash_salt)
            session_id = await _session_id(request) if route == "chat" else None
            for limit in limits_for(route, ip_hash, session_id, settings):
                if await hit(limit.bucket, limit.window_s) > limit.max_hits:
                    retry = _retry_after(limit.window_s)
                    raise HTTPException(
                        status_code=429,
                        detail=f"{limit.message} Please try again in {retry} seconds.",
                        headers={"Retry-After": str(retry)},
                    )
        except HTTPException:
            raise
        except Exception:
            log.warning("rate limiter unavailable; letting the request through", exc_info=True)

    return check


# One dependency per route, as module attributes so tests can override them.
limit_chat = limited("chat")
limit_voice = limited("voice")
limit_reset = limited("reset")
