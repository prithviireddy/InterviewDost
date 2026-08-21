"""
Alembic environment for async SQLAlchemy (asyncpg driver).
Settings are loaded from config.py so DATABASE_URL is always in sync.
The engine is created directly (not via async_engine_from_config) so we can
pass connect_args for SSL — required by Neon and other cloud Postgres providers.
"""
import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# ── Make the parent directory importable ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import models so their metadata is registered before Alembic inspects it.
import models  # noqa: F401
from database import Base, _prepare_async_url
from config import get_settings

settings = get_settings()

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Migration runners ─────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    async_url, _ = _prepare_async_url(settings.database_url)
    context.configure(
        url=async_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    async_url, connect_args = _prepare_async_url(settings.database_url)

    connectable = create_async_engine(
        async_url,
        connect_args=connect_args,
        poolclass=pool.NullPool,  # use a single connection per migration run
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
