"""github_to_google_oauth

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-25

Changes:
- Clear existing user data (GitHub IDs are incompatible with Google IDs)
- Rename column users.github_id -> users.google_id
- Add column users.email (VARCHAR 254, unique, not null)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Clear dependent data first (FK order: sessions -> users)
    op.execute("DELETE FROM sessions")
    op.execute("DELETE FROM payments")
    op.execute("DELETE FROM ats_checks")
    op.execute(
        "DELETE FROM messages WHERE interview_id IN (SELECT id FROM interviews)"
    )
    op.execute("DELETE FROM interviews")
    op.execute("DELETE FROM users")

    # 2. Rename github_id -> google_id
    op.alter_column(
        "users",
        "github_id",
        new_column_name="google_id",
        existing_type=sa.String(50),
        existing_nullable=False,
    )

    # 3. Add email column (not null — safe because we just cleared the table)
    op.add_column(
        "users",
        sa.Column("email", sa.String(254), nullable=False, server_default=""),
    )
    # Remove the server_default now that the column exists
    op.alter_column("users", "email", server_default=None)

    # 4. Add unique constraint on email
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "email")
    op.alter_column(
        "users",
        "google_id",
        new_column_name="github_id",
        existing_type=sa.String(50),
        existing_nullable=False,
    )
