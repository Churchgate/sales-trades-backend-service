"""add_trade_lead_review_status

New per-participant admin approval gate for Trade programs, independent of
crm_sync_status (delivery mechanism) and eligibility_status (document
completeness, its `approved`/`rejected` values currently unused). Programs
that opt in via config["require_admin_approval"] only push a participant to
Freshsales once an admin has explicitly approved them here — see
trade_crm_sync.sync_trade_lead.

Revision ID: 1b75a10512eb
Revises: a1c4e8f2d5b7
Create Date: 2026-07-29 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1b75a10512eb'
down_revision: Union[str, Sequence[str], None] = 'a1c4e8f2d5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'trade_leads',
        sa.Column('review_status', sa.String(), nullable=False, server_default='pending'),
    )
    op.add_column(
        'trade_leads',
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'trade_leads',
        sa.Column('reviewed_by', sa.String(), nullable=True),
    )
    op.create_index('idx_trade_leads_review_status', 'trade_leads', ['review_status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_trade_leads_review_status', table_name='trade_leads')
    op.drop_column('trade_leads', 'reviewed_by')
    op.drop_column('trade_leads', 'reviewed_at')
    op.drop_column('trade_leads', 'review_status')
