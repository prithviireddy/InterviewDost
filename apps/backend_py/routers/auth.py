"""
Auth router — Google OAuth flow, /me, /logout.

Routes (all prefixed with /api/v1 in main.py):
  GET  /auth/google           → redirect to Google OAuth consent screen
  GET  /auth/google/callback  → OAuth callback, create/find user, issue session
  POST /auth/logout           → delete session by Bearer token
  GET  /auth/me               → return current user info
"""
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from config import get_settings
from database import AsyncSessionLocal
from deps import CurrentUser
from models import Session as DBSession, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

# Google OAuth endpoints
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _generate_token() -> str:
    return secrets.token_hex(48)


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        t = header[7:].strip()
        return t or None
    return None


#  Routes

@router.get("/google")
async def google_login():
    """Redirect the browser to Google's OAuth consent screen."""
    state = _generate_token()
    redirect_uri = f"{settings.backend_url}/api/v1/auth/google/callback"

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(
        "oauth_state",
        state,
        httponly=True,
        samesite="lax",
        max_age=600,
        secure=settings.is_production,
    )
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """
    Google sends the user here after they authorise the app.
    We exchange the code for tokens, fetch the Google user profile,
    upsert our User record, create a Session, then redirect to the
    frontend with ?token=<session_token>.
    """
    # Handle user-denied consent
    if error:
        logger.warning("Google OAuth error from provider: %s", error)
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=google_denied",
            status_code=302,
        )

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    # Optional state validation
    cookie_state = request.cookies.get("oauth_state")
    if cookie_state and state != cookie_state:
        logger.warning("State mismatch in Google callback: cookie=%s state=%s", cookie_state, state)
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    redirect_uri = f"{settings.backend_url}/api/v1/auth/google/callback"

    # Exchange code for access token
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )

    if not token_resp.is_success:
        logger.error("Google token exchange failed: %s", token_resp.text)
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=google_auth_failed",
            status_code=302,
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        logger.error("No access_token in Google response: %s", token_data)
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=google_auth_failed",
            status_code=302,
        )

    # Fetch Google user profile
    async with httpx.AsyncClient(timeout=10) as client:
        user_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if not user_resp.is_success:
        logger.error("Google userinfo fetch failed: %s", user_resp.text)
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=google_api_failed",
            status_code=302,
        )

    guser = user_resp.json()
    google_id = str(guser.get("id", ""))
    email = str(guser.get("email", ""))
    name = str(guser.get("name") or guser.get("given_name") or email.split("@")[0])[:100]
    avatar_url = str(guser.get("picture", "") or "")[:500] or None

    if not google_id or not email:
        logger.error("Invalid Google user response: %s", guser)
        return RedirectResponse(
            url=f"{settings.frontend_url}/login?error=invalid_google_response",
            status_code=302,
        )

    # Upsert user + create session in a single transaction
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where((User.google_id == google_id) | (User.email == email))
        )
        user = result.scalar_one_or_none()

        if user:
            user.google_id = google_id
            user.email = email
            if avatar_url:
                user.avatar_url = avatar_url
            if name:
                user.username = name
        else:
            user = User(
                google_id=google_id,
                email=email,
                username=name,
                avatar_url=avatar_url,
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

    # Explicitly sanitize and add /?token= to ensure valid HTTP redirect header
    frontend_base = str(settings.frontend_url).strip().replace("\r", "").replace("\n", "").rstrip("/")
    redirect_url = f"{frontend_base}/?token={session.token}"
    logger.info("Google OAuth complete — redirecting to: %s", redirect_url)
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/logout")
async def logout(request: Request):
    """Delete the session identified by the Bearer token."""
    token = _extract_bearer(request)
    if token:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DBSession).where(DBSession.token == token)
            )
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
            "email": user.email,
            "avatarUrl": user.avatar_url,
            "credits": user.credits,
            "isUnlimited": user.is_unlimited,
        }
    }
