"""
ATS (Applicant Tracking System) router.

Routes (prefixed /api/v1 in main.py):
  POST /ats/check      → run ATS check, save, return result
  GET  /ats            → list user's ATS checks (last 50)
  GET  /ats/history    → same as /ats (duplicate preserved for API compat)
  GET  /ats/{id}       → get a single ATS check in detail
"""
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from deps import DB, CurrentUser
from models import AtsCheck, User
from services.groq import run_ats_check

router = APIRouter(tags=["ats"])

_CREDIT_COST_ATS = 2
_ID_RE = re.compile(r"^[a-zA-Z0-9-]+$")
_HTML_RE = re.compile(r"<[^>]*>")


async def _deduct_credits(db, user_id: str, amount: int) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.credits = max(0, user.credits - amount)
    await db.commit()


def _list_item(c: AtsCheck) -> dict:
    return {
        "id": c.id,
        "score": c.score,
        "summary": c.summary,
        "keywordMatches": c.keyword_matches or [],
        "missingSkills": c.missing_skills or [],
        "createdAt": c.created_at.isoformat(),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/ats/check")
async def ats_check(request: Request, user: CurrentUser, db: DB):
    if not user.is_unlimited and user.credits < _CREDIT_COST_ATS:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    body = await request.json()
    resume_text = body.get("resumeText", "")
    job_description = body.get("jobDescription", "")

    if (
        not resume_text
        or not job_description
        or len(resume_text) > 50_000
        or len(job_description) > 5_000
    ):
        raise HTTPException(status_code=400, detail="Invalid input")

    escaped_resume = _HTML_RE.sub("", resume_text)[:30_000]
    escaped_job = _HTML_RE.sub("", job_description)[:3_000]

    if not user.is_unlimited:
        await _deduct_credits(db, user.id, _CREDIT_COST_ATS)

    try:
        result = await run_ats_check(escaped_resume, escaped_job)
    except RuntimeError as exc:
        print(f"[ats] Groq error: {exc}")
        raise HTTPException(status_code=502, detail="AI service unavailable")

    check = AtsCheck(
        user_id=user.id,
        score=result["score"],
        keyword_matches=result["keyword_matches"],
        missing_skills=result["missing_skills"],
        suggestions=result["suggestions"],
        summary=result["summary"],
        resume_text=escaped_resume[:10_000],
        job_description=escaped_job,
    )
    db.add(check)
    await db.commit()
    await db.refresh(check)

    return {
        "id": check.id,
        "score": check.score,
        "keywordMatches": check.keyword_matches,
        "missingSkills": check.missing_skills,
        "suggestions": check.suggestions,
        "summary": check.summary,
    }


@router.get("/ats/history")
async def ats_history(user: CurrentUser, db: DB):
    result = await db.execute(
        select(AtsCheck)
        .where(AtsCheck.user_id == user.id)
        .order_by(AtsCheck.created_at.desc())
        .limit(50)
    )
    return {"checks": [_list_item(c) for c in result.scalars().all()]}


@router.get("/ats/{ats_id}")
async def get_ats_check(ats_id: str, user: CurrentUser, db: DB):
    if not ats_id or len(ats_id) > 100 or not _ID_RE.match(ats_id):
        raise HTTPException(status_code=400, detail="Invalid ID")

    result = await db.execute(
        select(AtsCheck).where(AtsCheck.id == ats_id, AtsCheck.user_id == user.id)
    )
    check = result.scalar_one_or_none()
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    return {
        "id": check.id,
        "score": check.score,
        "keywordMatches": check.keyword_matches or [],
        "missingSkills": check.missing_skills or [],
        "suggestions": check.suggestions or [],
        "summary": check.summary,
        "resumeText": check.resume_text,
        "jobDescription": check.job_description,
        "createdAt": check.created_at.isoformat(),
    }


@router.get("/ats")
async def list_ats(user: CurrentUser, db: DB):
    result = await db.execute(
        select(AtsCheck)
        .where(AtsCheck.user_id == user.id)
        .order_by(AtsCheck.created_at.desc())
        .limit(50)
    )
    return {"checks": [_list_item(c) for c in result.scalars().all()]}
