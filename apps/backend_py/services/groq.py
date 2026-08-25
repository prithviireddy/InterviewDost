"""
Groq LLM service — three functions:

  get_chat_completion  — next AI message in a live interview
  calculate_result     — score + feedback after interview ends
  run_ats_check        — ATS resume analysis
"""
import json
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from models import Interview, Message, MessageType

settings = get_settings()

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _groq_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }


def _build_context(interview: Interview) -> str:
    if interview.type.value == "GitHub" and interview.github_metadata:
        return (
            f"Here is the candidate's GitHub metadata for context:\n"
            f"{interview.github_metadata}"
        )
    if interview.type.value == "Resume" and interview.resume_text and interview.job_role:
        return (
            f"Here is the candidate's resume:\n{interview.resume_text}\n\n"
            f"Job role they're applying for:\n{interview.job_role}"
        )
    return "No additional context available."


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    return json.loads(cleaned)


# ── Public API ────────────────────────────────────────────────────────────────

async def get_chat_completion(interview_id: str, db: AsyncSession) -> str:
    """
    Build conversation history, call Groq, persist the AI message, and return
    the text content.
    """
    result = await db.execute(
        select(Interview).where(Interview.id == interview_id)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        raise ValueError("Interview not found")

    # Fetch all previous messages directly from DB to avoid session caching issues
    msg_result = await db.execute(
        select(Message)
        .where(Message.interview_id == interview_id)
        .order_by(Message.created_at.asc())
    )
    conversations = msg_result.scalars().all()
    context_block = _build_context(interview)

    system_content = (
        "You are an AI interviewer conducting a technical interview. Use English only.\n\n"
        f"{context_block}\n\n"
        "CRITICAL RULES - FOLLOW THESE EXACTLY:\n"
        "1. Ask ONLY ONE question at a time. Never ask multiple questions in a single message.\n"
        "2. Start with a brief greeting and ONE opening question.\n"
        "3. Wait for the candidate's answer before asking the next question.\n"
        "4. Keep your responses SHORT - at most 2-3 sentences.\n"
        "5. If the candidate gives a short or unclear answer, ask a friendly follow-up to help them elaborate.\n"
        "6. Do NOT repeat the same question if it wasn't answered. Instead, rephrase it gently.\n"
        "7. After the candidate answers, acknowledge their response briefly, then ask ONE follow-up or move to the next topic.\n"
        "8. Ask 3-4 questions total, one at a time. After the last answer, thank them and wrap up."
    )

    messages = [{"role": "system", "content": system_content}] + [
        {
            "role": "user" if c.type == MessageType.User else "assistant",
            "content": c.message,
        }
        for c in conversations
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _GROQ_URL,
            headers=_groq_headers(),
            json={
                "model": settings.groq_model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
        )

    if not resp.is_success:
        raise RuntimeError(f"Groq API error ({resp.status_code}): {resp.text[:200]}")

    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError("Empty response from Groq")

    ai_msg = Message(
        interview_id=interview_id,
        type=MessageType.Assistant,
        message=content,
    )
    db.add(ai_msg)
    await db.commit()

    return content


async def calculate_result(
    messages: list[dict],
    context: dict | None = None,
) -> dict:
    """
    Evaluate a completed interview transcript.
    Returns {"score": int 0-10, "feedback": str}.
    """
    ctx_str = "General interview"
    if context:
        parts = [f"Type: {context.get('type', 'Unknown')}"]
        if context.get("job_role"):
            parts.append("Job Description Provided")
        if context.get("type") == "GitHub" and context.get("github_metadata"):
            parts.append("GitHub Repos analyzed")
        ctx_str = ", ".join(parts)

    prompt = (
        "You are an expert evaluator. Your job is to evaluate the user's interview. "
        "Give them a score out of 10 and also let them know any feedback you have about their interview.\n\n"
        f"Interview context: {ctx_str}\n\n"
        "Please return only a JSON object with the following structure (no other text):\n"
        '{\n    "feedback": "your feedback here",\n    "score": <number between 0 and 10>\n}\n\n'
        f"Transcript:\n{json.dumps(messages)}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _GROQ_URL,
            headers=_groq_headers(),
            json={
                "model": settings.groq_model,
                "messages": [{"role": "system", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 1024,
            },
        )

    if not resp.is_success:
        raise RuntimeError(f"Groq API error ({resp.status_code})")

    text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    if not text:
        raise RuntimeError("Empty response from Groq")

    result = _extract_json(text)
    if not isinstance(result.get("score"), (int, float)) or not isinstance(
        result.get("feedback"), str
    ):
        raise RuntimeError("Invalid result format from Groq")

    return {
        "score": max(0, min(10, round(float(result["score"])))),
        "feedback": str(result["feedback"])[:5000],
    }


async def run_ats_check(resume_text: str, job_description: str) -> dict:
    """
    Analyse a resume against a job description with Groq.
    Returns a dict with score, keyword_matches, missing_skills, suggestions, summary.
    """
    prompt = (
        "You are an expert ATS resume checker. Analyze the resume against the job description and provide:\n"
        "1. An overall score (0-100)\n"
        "2. List of keyword matches found\n"
        "3. Missing important skills/keywords\n"
        "4. 3-5 specific suggestions for improvement\n"
        "5. A brief summary (2-3 sentences)\n\n"
        f"Resume:\n{resume_text}\n\n"
        f"Job Description:\n{job_description}\n\n"
        'Respond in JSON format: { "score": number, "keywordMatches": string[], '
        '"missingSkills": string[], "suggestions": string[], "summary": string }'
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _GROQ_URL,
            headers=_groq_headers(),
            json={
                "model": settings.groq_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert ATS checker. Return ONLY valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1024,
            },
        )

    if not resp.is_success:
        raise RuntimeError(f"Groq API error ({resp.status_code}): {resp.text[:200]}")

    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    json_match = re.search(r"\{[\s\S]*\}", content)
    if not json_match:
        raise RuntimeError(f"No JSON in Groq response: {content[:200]}")

    parsed = json.loads(json_match.group())

    score = max(0, min(100, int(parsed.get("score", 0))))
    keyword_matches = [str(k)[:200] for k in (parsed.get("keywordMatches") or [])[:50]]
    missing_skills = [str(k)[:200] for k in (parsed.get("missingSkills") or [])[:50]]
    suggestions = [str(k)[:500] for k in (parsed.get("suggestions") or [])[:10]]
    summary = str(parsed.get("summary", ""))[:1000]

    return {
        "score": score,
        "keyword_matches": keyword_matches,
        "missing_skills": missing_skills,
        "suggestions": suggestions,
        "summary": summary,
    }
