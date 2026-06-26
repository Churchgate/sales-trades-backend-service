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


_INDEXES = [
    ("idx_deals_snapshot_owner_stage_updated", ["owner_id", "stage_id", "stage_updated_at"]),
    ("idx_deals_snapshot_pipeline_close", ["pipeline_id", "expected_close_date"]),
    ("idx_deals_snapshot_created", ["deal_created_at"]),
]


def upgrade() -> None:
    """Upgrade schema.

    Build the indexes CONCURRENTLY so a deploy never fights table locks with the
    previous instance's still-running deal-sync writes (a blocking CREATE INDEX hung
    past the deploy healthcheck). CONCURRENTLY can't run in a transaction, so this runs
    in an autocommit block.

    `SET statement_timeout = 0`: CONCURRENTLY waits for in-flight transactions to drain,
    which can exceed the DB default (prod = 2min) and otherwise cancels the build.

    Each index is dropped (if it exists) before being recreated, to clear any INVALID
    index left behind by a previously interrupted concurrent build — IF NOT EXISTS alone
    would skip an invalid leftover and leave it unusable.
    """
    with op.get_context().autocommit_block():
        op.execute("SET statement_timeout = 0")
        op.execute("SET lock_timeout = 0")
        for name, columns in _INDEXES:
            op.drop_index(
                name, table_name="deals_snapshot", postgresql_concurrently=True, if_exists=True
            )
            op.create_index(
                name, "deals_snapshot", columns, postgresql_concurrently=True, if_not_exists=True
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
