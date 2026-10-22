"""add member submission fields to software and documents

Revision ID: 20261022_add_submission_fields
Revises: 20261021_add_slider_challenges
Create Date: 2026-10-22
"""
from alembic import op

revision = "20261022_add_submission_fields"
down_revision = "20261021_add_slider_challenges"
branch_labels = None
depends_on = None

# submission_status: "published" (admin uploads, the historical default),
# "pending" (member upload awaiting review), "rejected" (with a reason).

UPGRADES = (
    (
        "software",
        "ix_software_submitted_by",
    ),
    (
        "documents",
        "ix_documents_submitted_by",
    ),
)


def upgrade() -> None:
    for table, index_name in UPGRADES:
        op.execute(
            f"""
            ALTER TABLE {table} ADD COLUMN IF NOT EXISTS submitted_by BIGINT REFERENCES users(id)
            """
        )
        op.execute(
            f"""
            ALTER TABLE {table} ADD COLUMN IF NOT EXISTS submission_status VARCHAR(20) NOT NULL DEFAULT 'published'
            """
        )
        op.execute(
            f"""
            ALTER TABLE {table} ADD COLUMN IF NOT EXISTS rejected_reason TEXT
            """
        )
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} (submitted_by)")


def downgrade() -> None:
    for table, index_name in UPGRADES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS rejected_reason")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS submission_status")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS submitted_by")
