from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_settings = get_settings()
# NullPool: pytest-asyncio gives each test its own event loop, but a pooled
# asyncpg connection is bound to the loop it was created on. Reusing a pooled
# connection across tests raises "attached to a different loop". NullPool opens
# a fresh connection per checkout so no connection outlives a single test.
#
# statement_cache_size=0: required for the Supabase transaction pooler (pgbouncer).
# asyncpg names prepared statements deterministically (__asyncpg_stmt_N__); when
# pgbouncer reuses a server connection across checkouts, a fresh client connection
# re-issues the same name and raises DuplicatePreparedStatementError. Disabling the
# cache mirrors the app engine in app/core/database.py.
_test_engine = create_async_engine(
    _settings.test_database_url,
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0},
)
_TestSessionFactory = async_sessionmaker(bind=_test_engine, expire_on_commit=False)

# Truncate order respects FK references (deals_snapshot/stages -> pipelines, etc.)
# CASCADE handles the rest.
_TABLES = (
    "deal_events",
    "deals_snapshot",
    "email_activity",
    "tasks_snapshot",
    "pipeline_daily_snapshot",
    "dashboard_users",
    "stages",
    "pipelines",
    "owners",
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A real session against `TEST_DATABASE_URL`, truncated after each test."""
    async with _TestSessionFactory() as session:
        yield session

    async with _test_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
