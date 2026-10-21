"""add captcha_challenges table

Revision ID: 20261021_add_captcha_challenges
Revises: 20260630_add_keyence_inovance_mappings
Create Date: 2026-10-21
"""
from alembic import op

revision = "20261021_add_captcha_challenges"
down_revision = "20260630_add_keyence_inovance_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_captcha_challenges_created_at")
    op.execute("DROP TABLE IF EXISTS captcha_challenges")
