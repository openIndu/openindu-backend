"""add slider_challenges table

Revision ID: 20261021_add_slider_challenges
Revises: 20261021_drop_captcha_challenges
Create Date: 2026-10-21
"""
from alembic import op

revision = "20261021_add_slider_challenges"
down_revision = "20261021_drop_captcha_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS slider_challenges (
            id BIGSERIAL PRIMARY KEY,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            tolerance INTEGER NOT NULL DEFAULT 5,
            verified BOOLEAN NOT NULL DEFAULT FALSE,
            ticket VARCHAR(64),
            ticket_expires_at TIMESTAMP,
            is_used BOOLEAN NOT NULL DEFAULT FALSE,
            ip_address VARCHAR(64),
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_slider_challenges_ticket ON slider_challenges (ticket)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_slider_challenges_created_at ON slider_challenges (created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_slider_challenges_created_at")
    op.execute("DROP INDEX IF EXISTS ix_slider_challenges_ticket")
    op.execute("DROP TABLE IF EXISTS slider_challenges")
