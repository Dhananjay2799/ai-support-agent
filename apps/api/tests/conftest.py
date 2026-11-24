"""Shared fixtures. Database tests run against a real Postgres with pgvector.

Set TEST_DATABASE_URL to a disposable database that has had
supabase/local/bootstrap.sql and supabase/migrations/*.sql applied (CI does this).
Without it, database tests are skipped.
"""

import os
from collections.abc import AsyncIterator

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[AsyncConnection[DictRow]]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row, autocommit=True)
    try:
        yield conn
    finally:
        await conn.close()
