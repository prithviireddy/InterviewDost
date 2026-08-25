"""
Async SQLAlchemy engine + session factory.

Uses asyncpg under the hood. Handles Neon/cloud PostgreSQL URLs that carry
sslmode=require and channel_binding=require query params — asyncpg doesn't
accept those as URL params and requires ssl to be passed via connect_args.
"""
import ssl as ssl_lib
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

settings = get_settings()


def _prepare_async_url(url: str) -> tuple[str, dict]:
    """
    Convert a standard PostgreSQL connection URL for use with asyncpg.

    1. Swaps the scheme to postgresql+asyncpg://
    2. Strips params asyncpg doesn't understand: sslmode, channel_binding
    3. Returns (cleaned_url, connect_args) where connect_args carries an SSL
       context when the original URL requested SSL.
    """
    # ── 1. Scheme swap ────────────────────────────────────────────────────────
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
            break

    # ── 2. Strip incompatible query params ────────────────────────────────────
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    sslmode = (params.pop("sslmode", [None])[0] or "").lower()
    params.pop("channel_binding", None)  # not supported by asyncpg

    clean_query = urlencode({k: v[0] for k, v in params.items()})
    clean_url = urlunparse(parsed._replace(query=clean_query))

    # ── 3. Build connect_args ─────────────────────────────────────────────────
    connect_args: dict = {}
    if sslmode in ("require", "verify-ca", "verify-full", "prefer"):
        # Neon uses valid Let's Encrypt certs — standard SSL context works.
        connect_args["ssl"] = ssl_lib.create_default_context()

    return clean_url, connect_args


_async_url, _connect_args = _prepare_async_url(settings.database_url)

engine = create_async_engine(
    _async_url,
    connect_args=_connect_args,
    pool_size=10,
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=60,
    pool_pre_ping=True,
    echo=not settings.is_production,
)

# expire_on_commit=False keeps ORM objects accessible after commit in async code
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
