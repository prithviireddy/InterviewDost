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
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from models import Interview, InterviewStatus, Message, MessageType, Session as DBSession

logger = logging.getLogger(__name__)
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
    # ── 1. Validate auth & interview ownership with a clean scoped session ──
    async with AsyncSessionLocal() as db:
        user = await _auth(token, db)
        if not user:
            logger.warning("[ws/interview] Auth failed for token: %s", token[:10] if token else "None")
            await websocket.close(code=4001)
            return

        if not interviewId or len(interviewId) > 100 or not _ID_RE.match(interviewId):
            logger.warning("[ws/interview] Invalid interviewId format: %s", interviewId)
            await websocket.close(code=4000)
            return

        result = await db.execute(
            select(Interview)
            .where(Interview.id == interviewId)
            .options(selectinload(Interview.conversations))
        )
        interview = result.scalar_one_or_none()
        if not interview or interview.user_id != user.id:
            logger.warning("[ws/interview] User %s unauthorized for interview %s", user.id, interviewId)
            await websocket.close(code=4001)
            return

        has_conversations = len(interview.conversations) > 0
        username = user.username

    await websocket.accept()
    logger.info("[ws/interview] Client connected for interview %s (user: %s)", interviewId, username)

    # ── 2. Send greeting if this is a fresh interview ───────────────────────
    if not has_conversations:
        try:
            async with AsyncSessionLocal() as db:
                from services.groq import get_chat_completion
                logger.info("[ws/interview] Generating initial greeting for %s...", interviewId)
                greeting = await get_chat_completion(interviewId, db)
                await websocket.send_text(json.dumps({"type": "ai_message", "text": greeting}))
                logger.info("[ws/interview] Sent greeting (%d chars) to client", len(greeting))
        except Exception as exc:
            logger.error("[ws/interview] Greeting error: %s", exc)
            await websocket.send_text(
                json.dumps({"type": "error", "message": "Failed to start interview"})
            )

    # ── 3. Message loop (opens fresh DB session per message) ────────────────
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
            logger.info("[ws/interview] Received user speech: %r", sanitized)

            async with AsyncSessionLocal() as db:
                # Persist user message
                db.add(Message(
                    interview_id=interviewId,
                    type=MessageType.User,
                    message=sanitized,
                ))

                # Transition Pre → InProgress on first user message
                itv_res = await db.execute(select(Interview).where(Interview.id == interviewId))
                itv = itv_res.scalar_one_or_none()
                if itv and itv.status == InterviewStatus.Pre:
                    itv.status = InterviewStatus.InProgress

                await db.commit()

                try:
                    from services.groq import get_chat_completion
                    logger.info("[ws/interview] Calling Groq for next question...")
                    ai_text = await get_chat_completion(interviewId, db)
                    logger.info("[ws/interview] Groq responded: %r", ai_text[:120])
                    await websocket.send_text(json.dumps({"type": "ai_message", "text": ai_text}))
                except Exception as exc:
                    logger.error("[ws/interview] Groq error: %s", exc)
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Failed to process message"})
                    )

    except WebSocketDisconnect:
        logger.info("[ws/interview] WebSocket disconnected for interview %s", interviewId)
    except Exception as exc:
        logger.error("[ws/interview] Unexpected error: %s", exc)
