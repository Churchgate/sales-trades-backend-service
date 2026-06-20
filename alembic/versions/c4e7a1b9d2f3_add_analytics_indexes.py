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
    """Upgrade schema.

    Build the indexes CONCURRENTLY so a deploy never fights table locks with the
    previous instance's still-running deal-sync writes (a blocking CREATE INDEX hung
    past the deploy healthcheck). CONCURRENTLY can't run in a transaction, so each
    runs in an autocommit block; IF NOT EXISTS makes the migration safe to re-run.
    """
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_deals_snapshot_owner_stage_updated",
            "deals_snapshot",
            ["owner_id", "stage_id", "stage_updated_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "idx_deals_snapshot_pipeline_close",
            "deals_snapshot",
            ["pipeline_id", "expected_close_date"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "idx_deals_snapshot_created",
            "deals_snapshot",
            ["deal_created_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_deals_snapshot_created",
            table_name="deals_snapshot",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "idx_deals_snapshot_pipeline_close",
            table_name="deals_snapshot",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "idx_deals_snapshot_owner_stage_updated",
            table_name="deals_snapshot",
            postgresql_concurrently=True,
            if_exists=True,
        )
