"""
FastAPI dependency functions.

get_db  — yields an AsyncSession for the request lifetime.
get_current_user — reads the Bearer token, validates the session, returns the User.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from models import Session as DBSession, User


async def get_db():
    """Yield a database session and close it after the request."""
    async with AsyncSessionLocal() as session:
        yield session


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Validate a Bearer token and return the owning User.
    Raises HTTP 401 if the token is missing, invalid, or expired.
    """
    token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        candidate = authorization[7:].strip()
        token = candidate if candidate else None

    if not token or len(token) > 200:
        raise HTTPException(status_code=401, detail="Authentication required")

    result = await db.execute(
        select(DBSession)
        .where(DBSession.token == token)
        .options(selectinload(DBSession.user))
    )
    session_obj = result.scalar_one_or_none()

    if not session_obj:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    expires = session_obj.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return session_obj.user


# Type alias for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
