"""Add append-only submission history.

Revision ID: 20261023_add_submission_events
Revises: 20261022_add_submission_fields
"""
from alembic import op
import sqlalchemy as sa

revision = "20261023_add_submission_events"
down_revision = "20261022_add_submission_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submission_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_type", sa.String(20), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_name", sa.String(500), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("version", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_submission_events_actor_created", "submission_events", ["actor_id", "created_at"])
    op.create_index("ix_submission_events_resource", "submission_events", ["resource_type", "resource_id"])


def downgrade() -> None:
    op.drop_index("ix_submission_events_resource", table_name="submission_events")
    op.drop_index("ix_submission_events_actor_created", table_name="submission_events")
    op.drop_table("submission_events")
