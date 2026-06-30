"""add_lead_pack_delivery

Digital-pack delivery lifecycle for captured leads. The "Send Me the Digital
Pack" form promises the visitor their selected materials by email; these columns
let a background job (mirroring the CRM-sync lifecycle) email the assets and
record the outcome, so capture never blocks on email being reachable.

`pack_delivered_materials` records exactly which materials were emailed (resolved
against the campaign's asset map) next to the verbatim `requested_materials`, so
the request-vs-fulfilment gap is analysable per lead.

Revision ID: d7e9f1a2b3c4
Revises: b2c3d4e5f6a7
Create Date: 2026-06-30 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd7e9f1a2b3c4'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'leads',
        sa.Column(
            'pack_delivery_status',
            sa.String(),
            nullable=False,
            server_default='not_requested',
        ),
    )
    op.add_column(
        'leads',
        sa.Column('pack_delivered_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'leads',
        sa.Column(
            'pack_delivered_materials', postgresql.ARRAY(sa.String()), nullable=True
        ),
    )
    op.add_column(
        'leads', sa.Column('pack_delivery_error', sa.String(), nullable=True)
    )
    op.create_index(
        'idx_leads_campaign_pack', 'leads', ['campaign_id', 'pack_delivery_status']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_leads_campaign_pack', table_name='leads')
    op.drop_column('leads', 'pack_delivery_error')
    op.drop_column('leads', 'pack_delivered_materials')
    op.drop_column('leads', 'pack_delivered_at')
    op.drop_column('leads', 'pack_delivery_status')
