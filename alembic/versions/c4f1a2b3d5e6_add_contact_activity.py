"""add_contact_activity

Contact-level outreach activity (call / email / meeting / note) for NOG contacts,
which have no deals and so are invisible to the deal-centric activity sync. Populated
per-contact by `nog_activity_sync`; owner/tier are snapshotted so the dashboard can
group by salesperson and filter by Strategic/Standard without a live CRM call.

Revision ID: c4f1a2b3d5e6
Revises: bbb90d835871
Create Date: 2026-07-10 16:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c4f1a2b3d5e6'
down_revision: Union[str, Sequence[str], None] = 'bbb90d835871'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'contact_activity',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('campaign_id', sa.BigInteger(), nullable=False),
        sa.Column('contact_id', sa.BigInteger(), nullable=False),
        sa.Column('contact_name', sa.String(), nullable=True),
        sa.Column('owner_id', sa.BigInteger(), nullable=True),
        sa.Column('owner_name', sa.String(), nullable=True),
        sa.Column('prospect_tier', sa.String(), nullable=True),
        sa.Column('activity_type', sa.String(), nullable=False),
        sa.Column('direction', sa.String(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('subject', sa.String(), nullable=True),
        sa.Column('source_key', sa.String(), nullable=False),
        sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_contact_activity_owner',
        'contact_activity',
        ['campaign_id', 'owner_id', 'occurred_at'],
    )
    op.create_index(
        'idx_contact_activity_tier',
        'contact_activity',
        ['campaign_id', 'prospect_tier', 'occurred_at'],
    )
    op.create_index(
        'idx_contact_activity_source', 'contact_activity', ['source_key'], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_contact_activity_source', table_name='contact_activity')
    op.drop_index('idx_contact_activity_tier', table_name='contact_activity')
    op.drop_index('idx_contact_activity_owner', table_name='contact_activity')
    op.drop_table('contact_activity')
