"""
Interviews router.

Routes (prefixed /api/v1 in main.py):
  POST /pre-interview/github  → scrape GitHub, deduct credits, create interview
  POST /pre-interview/resume  → validate resume/job role, deduct credits, create interview
  GET  /interviews            → list user's interviews
  GET  /result/{interview_id} → get result; queue background eval if not Done
"""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from deps import DB, CurrentUser
from models import Interview, InterviewStatus, InterviewType, Message, MessageType, User
from services.github import scrape_github
from services.groq import calculate_result

router = APIRouter(tags=["interviews"])

CREDIT_COST = {"GitHub": 5, "Resume": 10, "ATS": 2}
_GITHUB_URL_RE = re.compile(r"^https?://(www\.)?github\.com/[a-zA-Z0-9_-]+/?$")
_GITHUB_USER_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ID_RE = re.compile(r"^[a-zA-Z0-9-]+$")


async def _deduct_credits(db: AsyncSession, user_id: str, amount: int) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.credits = max(0, user.credits - amount)
    await db.commit()


#  Routes

@router.post("/pre-interview/github", status_code=201)
async def pre_interview_github(request: Request, user: CurrentUser, db: DB):
    if not user.is_unlimited and user.credits < CREDIT_COST["GitHub"]:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    body = await request.json()
    github = body.get("github", "")

    if not github or not isinstance(github, str) or not _GITHUB_URL_RE.match(github):
        raise HTTPException(status_code=400, detail="Valid GitHub profile URL required")

    github_username = github.rstrip("/").rsplit("/", 1)[-1]
    if not _GITHUB_USER_RE.match(github_username):
        raise HTTPException(status_code=400, detail="Invalid GitHub username")

    github_data = await scrape_github(github_username)
    if not github_data:
        raise HTTPException(
            status_code=404,
            detail="GitHub profile not found or has no public repos",
        )

    if not user.is_unlimited:
        await _deduct_credits(db, user.id, CREDIT_COST["GitHub"])

    interview = Interview(
        user_id=user.id,
        type=InterviewType.GitHub,
        github_metadata=github_data,
        status=InterviewStatus.Pre,
    )
    db.add(interview)
    await db.commit()
    await db.refresh(interview)

    return {"id": interview.id}


@router.post("/pre-interview/resume", status_code=201)
async def pre_interview_resume(request: Request, user: CurrentUser, db: DB):
    if not user.is_unlimited and user.credits < CREDIT_COST["Resume"]:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    body = await request.json()
    resume_text = body.get("resumeText", "")
    job_role = body.get("jobRole", "")

    if (
        not resume_text
        or not job_role
        or not isinstance(resume_text, str)
        or not isinstance(job_role, str)
        or len(resume_text) > 50_000
        or len(job_role) > 10_000
    ):
        raise HTTPException(status_code=400, detail="Invalid request body")

    if not user.is_unlimited:
        await _deduct_credits(db, user.id, CREDIT_COST["Resume"])

    interview = Interview(
        user_id=user.id,
        type=InterviewType.Resume,
        job_role=job_role,
        resume_text=resume_text,
        status=InterviewStatus.Pre,
    )
    db.add(interview)
    await db.commit()
    await db.refresh(interview)

    return {"id": interview.id}


@router.get("/interviews")
async def list_interviews(user: CurrentUser, db: DB):
    result = await db.execute(
        select(Interview)
        .where(Interview.user_id == user.id)
        .order_by(Interview.created_at.desc())
        .options(selectinload(Interview.conversations))
    )
    interviews = result.scalars().all()

    return {
        "interviews": [
            {
                "id": i.id,
                "type": i.type.value,
                "score": i.score,
                "feedback": i.feedback,
                "status": i.status.value,
                "jobRole": i.job_role,
                "createdAt": i.created_at.isoformat(),
                "messageCount": len(i.conversations),
            }
            for i in interviews
        ]
    }


async def _compute_and_save_result(interview_id: str) -> None:
    """Background task: evaluate interview and persist score + feedback."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Interview)
                .where(Interview.id == interview_id)
                .options(selectinload(Interview.conversations))
            )
            interview = result.scalar_one_or_none()
            if not interview or interview.status == InterviewStatus.Done:
                return

            msgs = [
                {
                    "type": c.type.value,
                    "message": c.message,
                    "createdAt": c.created_at.isoformat(),
                }
                for c in sorted(interview.conversations, key=lambda m: m.created_at)
            ]

            evaluated = await calculate_result(
                msgs,
                {
                    "type": interview.type.value,
                    "job_role": interview.job_role,
                    "github_metadata": interview.github_metadata,
                    "resume_text": interview.resume_text,
                },
            )

            interview.status = InterviewStatus.Done
            interview.feedback = evaluated["feedback"]
            interview.score = evaluated["score"]
            await db.commit()

    except Exception as exc:
        print(f"[result-bg] Error for interview {interview_id}: {exc}")


@router.get("/result/{interview_id}")
async def get_result(
    interview_id: str,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: DB,
):
    if not interview_id or len(interview_id) > 100 or not _ID_RE.match(interview_id):
        raise HTTPException(status_code=400, detail="Invalid interview ID")

    result = await db.execute(
        select(Interview)
        .where(Interview.id == interview_id, Interview.user_id == user.id)
        .options(selectinload(Interview.conversations))
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    conversations = sorted(interview.conversations, key=lambda m: m.created_at)

    response = {
        "score": interview.score,
        "feedback": interview.feedback,
        "transcript": [
            {
                "type": c.type.value,
                "content": c.message,
                "createdAt": c.created_at.isoformat(),
            }
            for c in conversations
        ],
        "status": interview.status.value,
    }

    # Kick off evaluation in the background (mirrors original behaviour of
    # sending the response first, then calculating asynchronously).
    if interview.status != InterviewStatus.Done:
        background_tasks.add_task(_compute_and_save_result, interview_id)

    return response
