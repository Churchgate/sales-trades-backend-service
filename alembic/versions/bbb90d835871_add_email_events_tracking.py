"""add_email_events_tracking

SendGrid Event Webhook (open/click) tracking for campaign emails. A new
`email_events` table keeps the full per-event history (incl. which document a
click was for), while `leads` gets lightweight rollup columns
(`pack_opened_at`/`pack_opened_count`/`pack_clicked_materials`) for dashboard
convenience without a join on the common path.

Revision ID: bbb90d835871
Revises: d7e9f1a2b3c4
Create Date: 2026-07-08 14:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bbb90d835871'
down_revision: Union[str, Sequence[str], None] = 'd7e9f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'email_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.BigInteger(), nullable=False),
        sa.Column('email_kind', sa.String(), nullable=True),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('material', sa.String(), nullable=True),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('sg_event_id', sa.String(), nullable=False),
        sa.Column('sg_message_id', sa.String(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')
        ),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_email_events_lead', 'email_events', ['lead_id', 'occurred_at']
    )
    op.create_index(
        'idx_email_events_sg_event_id', 'email_events', ['sg_event_id'], unique=True
    )

    op.add_column(
        'leads',
        sa.Column('pack_opened_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'leads',
        sa.Column(
            'pack_opened_count', sa.Integer(), nullable=False, server_default='0'
        ),
    )
    op.add_column(
        'leads',
        sa.Column(
            'pack_clicked_materials', postgresql.ARRAY(sa.String()), nullable=True
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('leads', 'pack_clicked_materials')
    op.drop_column('leads', 'pack_opened_count')
    op.drop_column('leads', 'pack_opened_at')
    op.drop_index('idx_email_events_sg_event_id', table_name='email_events')
    op.drop_index('idx_email_events_lead', table_name='email_events')
    op.drop_table('email_events')
