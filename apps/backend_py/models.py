"""
SQLAlchemy 2.0 ORM models — mirrors the Prisma schema exactly.

Table / column names use snake_case (PostgreSQL convention).
Python-side enum values preserve the exact strings from the Prisma schema so
that the API responses are identical to the original TypeScript backend.
"""
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Any, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


# Enums 

class MessageType(str, PyEnum):
    User = "User"
    Assistant = "Assistant"


class InterviewType(str, PyEnum):
    GitHub = "GitHub"
    Resume = "Resume"


class InterviewStatus(str, PyEnum):
    Pre = "Pre"
    InProgress = "InProgress"
    Done = "Done"


#  Helpers 

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


#  Models 

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    google_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    credits: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    is_unlimited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    interviews: Mapped[List["Interview"]] = relationship(back_populates="user", lazy="select")
    sessions: Mapped[List["Session"]] = relationship(back_populates="user", lazy="select")
    payments: Mapped[List["Payment"]] = relationship(back_populates="user", lazy="select")
    ats_checks: Mapped[List["AtsCheck"]] = relationship(back_populates="user", lazy="select")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    token: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship(back_populates="sessions")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    razorpay_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="created", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="payments")


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    type: Mapped[InterviewType] = mapped_column(
        SAEnum(InterviewType, name="interviewtype", create_type=False), nullable=False
    )
    github_metadata: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    job_role: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resume_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[InterviewStatus] = mapped_column(
        SAEnum(InterviewStatus, name="interviewstatus", create_type=False), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="interviews")
    conversations: Mapped[List["Message"]] = relationship(
        back_populates="interview", lazy="select"
    )


class AtsCheck(Base):
    __tablename__ = "ats_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    resume_text: Mapped[str] = mapped_column(Text, nullable=False)
    job_description: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    keyword_matches: Mapped[Any] = mapped_column(JSONB, nullable=False)
    missing_skills: Mapped[Any] = mapped_column(JSONB, nullable=False)
    suggestions: Mapped[Any] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="ats_checks")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[MessageType] = mapped_column(
        SAEnum(MessageType, name="messagetype", create_type=False), nullable=False
    )
    interview_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("interviews.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    interview: Mapped["Interview"] = relationship(back_populates="conversations")
