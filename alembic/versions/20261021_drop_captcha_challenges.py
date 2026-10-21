"""drop captcha_challenges table (superseded by slider_challenges)

Revision ID: 20261021_drop_captcha_challenges
Revises: 20261021_add_captcha_challenges
Create Date: 2026-10-21
"""
from alembic import op

revision = "20261021_drop_captcha_challenges"
down_revision = "20261021_add_captcha_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS captcha_challenges")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS captcha_challenges (
            id BIGSERIAL PRIMARY KEY,
            answer VARCHAR(8) NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            is_used BOOLEAN NOT NULL DEFAULT FALSE,
            ip_address VARCHAR(64),
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_captcha_challenges_created_at "
        "ON captcha_challenges (created_at)"
    )
