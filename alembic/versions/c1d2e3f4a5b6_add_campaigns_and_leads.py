"""add_campaigns_and_leads

Booth/stand lead-capture (WTC Abuja Interactive Stand App). `campaigns` is the
reusable per-event container (form/content live in a JSONB config); `leads` is
the captured visitors, with normalised columns for stats/CSV/CRM plus a raw
`responses` JSONB kept verbatim for forward-compat and a future AI layer.

Revision ID: c1d2e3f4a5b6
Revises: b8f4d2a6c1e3
Create Date: 2026-06-23 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b8f4d2a6c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'campaigns',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('starts_on', sa.Date(), nullable=True),
        sa.Column('ends_on', sa.Date(), nullable=True),
        sa.Column('timezone', sa.String(), nullable=False),
        sa.Column(
            'config', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_campaigns_slug', 'campaigns', ['slug'], unique=True)

    op.create_table(
        'leads',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('campaign_id', sa.BigInteger(), nullable=False),
        sa.Column('first_name', sa.String(), nullable=False),
        sa.Column('last_name', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('phone', sa.String(), nullable=False),
        sa.Column('company', sa.String(), nullable=False),
        sa.Column('job_title', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('device_id', sa.String(), nullable=True),
        sa.Column('timing', sa.String(), nullable=True),
        sa.Column('interests', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('requested_materials', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('tags', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('inspection_requested', sa.Boolean(), nullable=False),
        sa.Column('inspection_type', sa.String(), nullable=True),
        sa.Column('marketing_opt_in', sa.Boolean(), nullable=False),
        sa.Column('consent_status', sa.Boolean(), nullable=False),
        sa.Column('consent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            'responses', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column('crm_sync_status', sa.String(), nullable=False),
        sa.Column('crm_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('crm_contact_id', sa.String(), nullable=True),
        sa.Column('crm_error', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_leads_campaign_created', 'leads', ['campaign_id', 'created_at'])
    op.create_index('idx_leads_campaign_sync', 'leads', ['campaign_id', 'crm_sync_status'])
    op.create_index(
        'idx_leads_campaign_email', 'leads', ['campaign_id', 'email'], unique=True
    )
    op.create_index(
        'idx_leads_interests', 'leads', ['interests'], postgresql_using='gin'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_leads_interests', table_name='leads')
    op.drop_index('idx_leads_campaign_email', table_name='leads')
    op.drop_index('idx_leads_campaign_sync', table_name='leads')
    op.drop_index('idx_leads_campaign_created', table_name='leads')
    op.drop_table('leads')
    op.drop_index('ix_campaigns_slug', table_name='campaigns')
    op.drop_table('campaigns')
