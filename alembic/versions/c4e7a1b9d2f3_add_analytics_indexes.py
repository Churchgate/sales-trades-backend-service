"""add_analytics_indexes

Composite/covering indexes for the read-heavy analytics aggregations (spec §B0):
owner+stage+stage-move for staleness/owner views, pipeline+expected-close for the
revenue-by-month forecast, and deal_created_at for first-response-time.

Revision ID: c4e7a1b9d2f3
Revises: ad205b8f1f36
Create Date: 2026-06-18 09:40:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e7a1b9d2f3'
down_revision: Union[str, Sequence[str], None] = 'ad205b8f1f36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Disable timeouts so index creation can wait for locks held by a running
    # deal sync job during rolling restarts (mirrors migration ad205b8f1f36).
    op.execute("SET statement_timeout = 0")
    op.execute("SET lock_timeout = 0")
    op.create_index(
        "idx_deals_snapshot_owner_stage_updated",
        "deals_snapshot",
        ["owner_id", "stage_id", "stage_updated_at"],
    )
    op.create_index(
        "idx_deals_snapshot_pipeline_close",
        "deals_snapshot",
        ["pipeline_id", "expected_close_date"],
    )
    op.create_index(
        "idx_deals_snapshot_created",
        "deals_snapshot",
        ["deal_created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_deals_snapshot_created", table_name="deals_snapshot")
    op.drop_index("idx_deals_snapshot_pipeline_close", table_name="deals_snapshot")
    op.drop_index("idx_deals_snapshot_owner_stage_updated", table_name="deals_snapshot")
