"""Initial schema — creates all tables and enum types from scratch.

Revision ID: 0001
Revises:
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── PostgreSQL enum types ─────────────────────────────────────────────────
    op.execute("CREATE TYPE messagetype    AS ENUM ('User', 'Assistant')")
    op.execute("CREATE TYPE interviewtype  AS ENUM ('GitHub', 'Resume')")
    op.execute("CREATE TYPE interviewstatus AS ENUM ('Pre', 'InProgress', 'Done')")

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",           sa.String(36),  primary_key=True),
        sa.Column("github_id",    sa.String(50),  nullable=False),
        sa.Column("username",     sa.String(100), nullable=False),
        sa.Column("avatar_url",   sa.String(500), nullable=True),
        sa.Column("credits",      sa.Integer(),   nullable=False, server_default="50"),
        sa.Column("is_unlimited", sa.Boolean(),   nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("github_id", name="uq_users_github_id"),
    )

    # ── sessions ──────────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("id",         sa.String(36),  primary_key=True),
        sa.Column("token",      sa.String(200), nullable=False),
        sa.Column("user_id",    sa.String(36),  sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token", name="uq_sessions_token"),
    )

    # ── payments ──────────────────────────────────────────────────────────────
    op.create_table(
        "payments",
        sa.Column("id",           sa.String(36),  primary_key=True),
        sa.Column("user_id",      sa.String(36),  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("razorpay_id",  sa.String(200), nullable=False),
        sa.Column("amount",       sa.Integer(),   nullable=False),
        sa.Column("credits",      sa.Integer(),   nullable=False),
        sa.Column("tier",         sa.String(50),  nullable=False),
        sa.Column("status",       sa.String(50),  nullable=False, server_default="'created'"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("razorpay_id", name="uq_payments_razorpay_id"),
    )

    # ── interviews ────────────────────────────────────────────────────────────
    op.create_table(
        "interviews",
        sa.Column("id",              sa.String(36), primary_key=True),
        sa.Column("user_id",         sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "type",
            postgresql.ENUM("GitHub", "Resume", name="interviewtype", create_type=False),
            nullable=False,
        ),
        sa.Column("github_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("job_role",        sa.Text(), nullable=True),
        sa.Column("resume_text",     sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("Pre", "InProgress", "Done", name="interviewstatus", create_type=False),
            nullable=False,
        ),
        sa.Column("score",    sa.Integer(), nullable=False, server_default="0"),
        sa.Column("feedback", sa.Text(),    nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── messages ──────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id",      sa.String(36), primary_key=True),
        sa.Column("message", sa.Text(),     nullable=False),
        sa.Column(
            "type",
            postgresql.ENUM("User", "Assistant", name="messagetype", create_type=False),
            nullable=False,
        ),
        sa.Column("interview_id", sa.String(36), sa.ForeignKey("interviews.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── ats_checks ────────────────────────────────────────────────────────────
    op.create_table(
        "ats_checks",
        sa.Column("id",               sa.String(36), primary_key=True),
        sa.Column("user_id",          sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("resume_text",      sa.Text(),     nullable=False),
        sa.Column("job_description",  sa.Text(),     nullable=False),
        sa.Column("score",            sa.Integer(),  nullable=False),
        sa.Column("keyword_matches",  postgresql.JSONB(), nullable=False),
        sa.Column("missing_skills",   postgresql.JSONB(), nullable=False),
        sa.Column("suggestions",      postgresql.JSONB(), nullable=False),
        sa.Column("summary",          sa.Text(),     nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    op.create_index("ix_sessions_token",        "sessions",   ["token"])
    op.create_index("ix_interviews_user_id",    "interviews", ["user_id"])
    op.create_index("ix_messages_interview_id", "messages",   ["interview_id"])
    op.create_index("ix_ats_checks_user_id",    "ats_checks", ["user_id"])


def downgrade() -> None:
    op.drop_table("ats_checks")
    op.drop_table("messages")
    op.drop_table("interviews")
    op.drop_table("payments")
    op.drop_table("sessions")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS messagetype")
    op.execute("DROP TYPE IF EXISTS interviewtype")
    op.execute("DROP TYPE IF EXISTS interviewstatus")
