"""
/api/v1/ws — authenticated real-time interview WebSocket.

Query params:
  token       — Bearer token (same as HTTP Authorization header)
  interviewId — UUID of the Interview to join

Protocol (JSON text frames):
  Client → Server: { "type": "user_message", "text": "..." }
  Server → Client: { "type": "ai_message",   "text": "..." }
                   { "type": "error",         "message": "..." }
"""
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from models import Interview, InterviewStatus, Message, MessageType, Session as DBSession

router = APIRouter()

_ID_RE = re.compile(r"^[a-zA-Z0-9-]+$")
_HTML_RE = re.compile(r"<[^>]*>")


async def _auth(token: str | None, db) -> object | None:
    """Return the User for *token* or None if auth fails."""
    if not token or len(token) > 200:
        return None
    result = await db.execute(
        select(DBSession)
        .where(DBSession.token == token)
        .options(selectinload(DBSession.user))
    )
    session = result.scalar_one_or_none()
    if not session:
        return None
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    return session.user


@router.websocket("/api/v1/ws")
async def interview_ws(
    websocket: WebSocket,
    token: str | None = Query(None),
    interviewId: str | None = Query(None),
):
    # We manage the DB session manually because the connection is long-lived.
    async with AsyncSessionLocal() as db:
        # ── Authentication ──────────────────────────────────────────────────
        user = await _auth(token, db)
        if not user:
            await websocket.close(code=4001)
            return

        # ── Validate interviewId ────────────────────────────────────────────
        if not interviewId or len(interviewId) > 100 or not _ID_RE.match(interviewId):
            await websocket.close(code=4000)
            return

        # ── Authorise interview ownership ───────────────────────────────────
        result = await db.execute(
            select(Interview)
            .where(Interview.id == interviewId)
            .options(selectinload(Interview.conversations))
        )
        interview = result.scalar_one_or_none()
        if not interview or interview.user_id != user.id:
            await websocket.close(code=4001)
            return

        await websocket.accept()

        # ── Send greeting if this is a fresh interview ──────────────────────
        conversations = sorted(interview.conversations, key=lambda m: m.created_at)
        if not conversations:
            try:
                from services.groq import get_chat_completion  # lazy import
                greeting = await get_chat_completion(interviewId, db)
                await websocket.send_text(json.dumps({"type": "ai_message", "text": greeting}))
            except Exception as exc:
                print(f"[ws/interview] greeting error: {exc}")
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Failed to start interview"})
                )

        # ── Message loop ────────────────────────────────────────────────────
        try:
            while True:
                raw_data = await websocket.receive_text()

                try:
                    payload = json.loads(raw_data)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Invalid JSON"})
                    )
                    continue

                if (
                    payload.get("type") != "user_message"
                    or not payload.get("text")
                    or not isinstance(payload.get("text"), str)
                ):
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Invalid message format"})
                    )
                    continue

                sanitized = _HTML_RE.sub("", payload["text"])[:2000]

                # Persist user message
                db.add(Message(
                    interview_id=interviewId,
                    type=MessageType.User,
                    message=sanitized,
                ))
                await db.commit()

                # Transition Pre → InProgress on first user message
                if interview.status == InterviewStatus.Pre:
                    interview.status = InterviewStatus.InProgress
                    await db.commit()

                try:
                    from services.groq import get_chat_completion
                    ai_text = await get_chat_completion(interviewId, db)
                    await websocket.send_text(json.dumps({"type": "ai_message", "text": ai_text}))
                except Exception as exc:
                    print(f"[ws/interview] groq error: {exc}")
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Failed to process message"})
                    )

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            print(f"[ws/interview] unexpected error: {exc}")
