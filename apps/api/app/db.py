"""Postgres access. One small async pool per process.

`prepare_threshold=None` disables server-side prepared statements, which Supabase's
transaction pooler (port 6543) doesn't support. Serverless instances are short-lived
and numerous, so the pool stays tiny and the pooler does the real pooling.
"""

import asyncio

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


async def get_pool() -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    """One pool per event loop (serverless runtimes may start a new loop)."""
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is None or _pool_loop is not loop:
        _pool_loop = loop
        url = get_settings().database_url
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = AsyncConnectionPool(
            url,
            min_size=0,
            max_size=4,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": True},
            connection_class=AsyncConnection[DictRow],
            open=False,
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
