"""add_lead_scoring_and_triage

Persists engagement_score (previously computed per-request in Python, never
SQL-sortable — the reason the dashboard capped a "score" sort at a 500-row
client-side window) plus the ICP fit score/rationale from the OpenRouter
scoring layer, and a triage status so reps can mark a lead contacted/dismissed
without touching CRM sync or pack delivery.

Revision ID: 755cca72bb41
Revises: c4f1a2b3d5e6
Create Date: 2026-07-20 11:37:43.294377

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '755cca72bb41'
down_revision: Union[str, Sequence[str], None] = 'c4f1a2b3d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('leads', sa.Column('engagement_score', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('leads', sa.Column('icp_score', sa.Integer(), nullable=True))
    op.add_column('leads', sa.Column('icp_tier', sa.String(), nullable=True))
    op.add_column('leads', sa.Column('icp_rationale', sa.String(), nullable=True))
    op.add_column('leads', sa.Column('score_computed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('leads', sa.Column('triage_status', sa.String(), nullable=False, server_default='new'))
    op.add_column('leads', sa.Column('triage_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('leads', sa.Column('triage_by', sa.String(), nullable=True))
    # Cross-campaign hot-leads queue sorts/filters by this — the main blocker
    # this migration removes (every lead query today is campaign-scoped).
    op.create_index('idx_leads_engagement_score', 'leads', ['engagement_score'])
    op.create_index('idx_leads_triage_status', 'leads', ['triage_status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_leads_triage_status', table_name='leads')
    op.drop_index('idx_leads_engagement_score', table_name='leads')
    op.drop_column('leads', 'triage_by')
    op.drop_column('leads', 'triage_at')
    op.drop_column('leads', 'triage_status')
    op.drop_column('leads', 'score_computed_at')
    op.drop_column('leads', 'icp_rationale')
    op.drop_column('leads', 'icp_tier')
    op.drop_column('leads', 'icp_score')
    op.drop_column('leads', 'engagement_score')
