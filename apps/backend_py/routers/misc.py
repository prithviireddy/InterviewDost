"""
Miscellaneous routes:
  GET  /ping
  GET  /pricing
  GET  /credits
  GET  /dashboard/stats
  GET  /analytics
  POST /tts
"""
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

from config import get_settings
from deps import DB, CurrentUser
from models import Interview, InterviewStatus, InterviewType, User

router = APIRouter(tags=["misc"])
settings = get_settings()

CREDIT_COST = {"GitHub": 5, "Resume": 10, "ATS": 2}

PRICING_PLANS: list[dict] = [
    {"id": "free",      "name": "Free",         "credits": 50,    "price": 0,    "popular": False},
    {"id": "starter",   "name": "Starter",       "credits": 500,   "price": 499,  "popular": True},
    {"id": "pro",       "name": "Professional",  "credits": 2000,  "price": 1499, "popular": False},
    {"id": "unlimited", "name": "Unlimited",     "credits": -1,    "price": 2999, "popular": False},
]

# Skill keyword patterns (same regex as the original TypeScript backend)
_SKILL_PATTERNS: dict[str, re.Pattern] = {
    "Data Structures":  re.compile(r"array|linked list|stack|queue|tree|graph|hash|heap|trie", re.I),
    "Algorithms":       re.compile(r"sort|search|recursion|dp|dynamic.program|greedy|backtrack|divide|conquer", re.I),
    "System Design":    re.compile(r"scalab|distributed|microservice|load.balanc|cache|database.shard|consistenthash", re.I),
    "Databases":        re.compile(r"sql|nosql|index|query|normaliz|transaction|acid|mongodb|postgres|mysql", re.I),
    "Web Dev":          re.compile(r"react|api|rest|graphql|http|frontend|backend|full.stack|express|next", re.I),
    "Problem Solving":  re.compile(r"complexity|optimize|refactor|edge.case|brute.force|efficient", re.I),
}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/ping")
async def ping():
    return {"ok": True}


@router.get("/pricing")
async def pricing():
    return {"plans": PRICING_PLANS}


@router.get("/credits")
async def get_credits(user: CurrentUser, db: DB):
    result = await db.execute(
        select(User.credits, User.is_unlimited).where(User.id == user.id)
    )
    row = result.one_or_none()
    return {
        "credits":     row.credits if row else 0,
        "isUnlimited": row.is_unlimited if row else False,
        "costs":       CREDIT_COST,
    }


@router.get("/dashboard/stats")
async def dashboard_stats(user: CurrentUser, db: DB):
    result = await db.execute(
        select(Interview)
        .where(Interview.user_id == user.id)
        .order_by(Interview.created_at.asc())
    )
    interviews = result.scalars().all()

    completed = [i for i in interviews if i.status == InterviewStatus.Done]
    total_score = sum(i.score for i in completed)
    average_score = round((total_score / len(completed)) * 10) / 10 if completed else 0

    return {
        "totalInterviews":     len(interviews),
        "completedInterviews": len(completed),
        "averageScore":        average_score,
        "scoresOverTime": [
            {"date": i.created_at.isoformat(), "score": i.score, "type": i.type.value}
            for i in completed
        ],
        "typeBreakdown": {
            "github": sum(1 for i in interviews if i.type == InterviewType.GitHub),
            "resume": sum(1 for i in interviews if i.type == InterviewType.Resume),
        },
        "statusCount": {
            "completed":  len(completed),
            "inProgress": sum(1 for i in interviews if i.status == InterviewStatus.InProgress),
            "pre":        sum(1 for i in interviews if i.status == InterviewStatus.Pre),
        },
    }


@router.get("/analytics")
async def analytics(user: CurrentUser, db: DB):
    result = await db.execute(
        select(Interview)
        .where(Interview.user_id == user.id, Interview.status == InterviewStatus.Done)
        .order_by(Interview.created_at.asc())
    )
    interviews = result.scalars().all()

    score_list = [i.score for i in interviews]
    best_score  = max(score_list) if score_list else 0
    avg_score   = round((sum(score_list) / len(score_list)) * 10) / 10 if score_list else 0

    recent_3    = score_list[-3:]
    recent_avg  = round((sum(recent_3) / len(recent_3)) * 10) / 10 if recent_3 else 0

    improvement = (
        round((score_list[-1] - score_list[0]) * 10) / 10 if len(score_list) >= 2 else 0
    )

    skill_scores: dict[str, list[int]] = {k: [] for k in _SKILL_PATTERNS}
    for itv in interviews:
        combined = f"{itv.feedback or ''} {itv.job_role or ''} {itv.resume_text or ''}"
        for skill, pattern in _SKILL_PATTERNS.items():
            if pattern.search(combined):
                skill_scores[skill].append(itv.score)

    radar_data = sorted(
        [
            {
                "skill":      skill,
                "score":      round((sum(scores) / len(scores)) * 10) / 10 if scores else 0,
                "interviews": len(scores),
            }
            for skill, scores in skill_scores.items()
        ],
        key=lambda x: x["score"],
        reverse=True,
    )

    return {
        "totalCompleted":   len(interviews),
        "bestScore":        best_score,
        "averageScore":     avg_score,
        "recentAverage":    recent_avg,
        "improvement":      improvement,
        "scoreList":        score_list,
        "radarData":        radar_data,
        "feedbacks":        [i.feedback for i in interviews if i.feedback],
        "recentInterviews": [
            {
                "id":        i.id,
                "score":     i.score,
                "type":      i.type.value,
                "jobRole":   i.job_role,
                "createdAt": i.created_at.isoformat(),
            }
            for i in list(reversed(interviews[-5:]))
        ],
    }


@router.post("/tts")
async def tts(request: Request):
    body = await request.json()
    text = body.get("text", "")

    if not isinstance(text, str) or len(text) < 1 or len(text) > 3000:
        raise HTTPException(status_code=400, detail="Invalid request body")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.deepgram.com/v1/speak",
                headers={
                    "Authorization": f"Token {settings.deepgram_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={"text": text},
            )

        if not resp.is_success:
            raise HTTPException(status_code=502, detail="TTS service error")

        return Response(
            content=resp.content,
            media_type="audio/mpeg",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="TTS request timed out")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="TTS failed")
