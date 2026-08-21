"""
Auth router — GitHub OAuth flow, /me, /logout.

Routes (all prefixed with /api/v1 in main.py):
  GET  /auth/github           → redirect to GitHub OAuth page
  GET  /auth/github/callback  → OAuth callback, create/find user, issue session
  POST /auth/logout           → delete session by Bearer token
  GET  /auth/me               → return current user info
"""
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import AsyncSessionLocal
from deps import DB, CurrentUser, get_db
from models import Session as DBSession, User

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _generate_token() -> str:
    return secrets.token_hex(48)


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        t = header[7:].strip()
        return t or None
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/github")
async def github_login():
    """Redirect the browser to GitHub's OAuth authorization page."""
    state = _generate_token()
    redirect_uri = f"{settings.backend_url}/api/v1/auth/github/callback"
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&scope=read:user"
        f"&state={state}"
    )
    resp = RedirectResponse(url=url)
    resp.set_cookie(
        "oauth_state",
        state,
        httponly=True,
        samesite="lax",
        max_age=600,
        secure=settings.is_production,
    )
    return resp


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
):
    """
    GitHub sends the user here after they authorise the app.
    We exchange the code for an access token, fetch the GitHub user profile,
    upsert our User record, create a Session, then redirect to the frontend
    with ?token=<session_token>.
    """
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    cookie_state = request.cookies.get("oauth_state")
    if cookie_state and state != cookie_state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # Exchange code for access token
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=github_auth_failed"
        )

    # Fetch GitHub user profile
    async with httpx.AsyncClient(timeout=10) as client:
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if not user_resp.is_success:
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=github_api_failed"
        )

    gh = user_resp.json()
    if not gh.get("id") or not gh.get("login"):
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=invalid_github_response"
        )

    # Upsert user + create session in a single transaction
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.github_id == str(gh["id"]))
        )
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                github_id=str(gh["id"]),
                username=str(gh["login"])[:100],
                avatar_url=(str(gh.get("avatar_url", "")) or None)[:500],
                credits=50,
            )
            db.add(user)
            await db.flush()  # get user.id before creating the session

        session = DBSession(
            user_id=user.id,
            token=_generate_token(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

    return RedirectResponse(url=f"{settings.frontend_url}?token={session.token}")


@router.post("/logout")
async def logout(request: Request, db: DB):
    """Delete the session identified by the Bearer token."""
    token = _extract_bearer(request)
    if token:
        result = await db.execute(select(DBSession).where(DBSession.token == token))
        session = result.scalar_one_or_none()
        if session:
            await db.delete(session)
            await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(user: CurrentUser):
    """Return the authenticated user's profile."""
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "avatarUrl": user.avatar_url,
            "credits": user.credits,
            "isUnlimited": user.is_unlimited,
        }
    }
