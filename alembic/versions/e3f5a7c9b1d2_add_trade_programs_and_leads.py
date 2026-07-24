"""add_trade_programs_and_leads

Trade is a separate area from campaigns/leads: Trade contacts (starting with
Export Launchpad Boot Camp 2026) have a different direction — export
readiness / eligibility screening — than the workspace/residential leads
captured through campaigns, so they get their own tables rather than being
mixed into `leads`.

`trade_leads` holds one row PER PARTICIPANT (a registration may name up to
two — a required primary and an optional second), linked by
`registration_id`/`participant_index` rather than a parent/child table, so
each participant stays independently listable, filterable and CRM-syncable.
The email-uniqueness index is partial (`WHERE email <> ''`) because the
second participant's email is optional.

Also adds `email_events.trade_lead_id` so the existing Export Launchpad
campaign's open/click history can be re-pointed at the new trade rows when
`scripts/transfer_export_launchpad.py` migrates those registrations over.

Revision ID: e3f5a7c9b1d2
Revises: 755cca72bb41
Create Date: 2026-07-24 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e3f5a7c9b1d2'
down_revision: Union[str, Sequence[str], None] = '755cca72bb41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'trade_programs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
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
    op.create_index('ix_trade_programs_slug', 'trade_programs', ['slug'], unique=True)

    op.create_table(
        'trade_leads',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_program_id', sa.BigInteger(), nullable=False),
        sa.Column('registration_id', sa.String(), nullable=False),
        sa.Column('participant_index', sa.Integer(), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('first_name', sa.String(), nullable=False),
        sa.Column('middle_name', sa.String(), nullable=True),
        sa.Column('last_name', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('phone', sa.String(), nullable=True),
        sa.Column('job_title', sa.String(), nullable=True),
        sa.Column('company', sa.String(), nullable=True),
        sa.Column('registered_address', sa.String(), nullable=True),
        sa.Column('city', sa.String(), nullable=True),
        sa.Column('postal_code', sa.String(), nullable=True),
        sa.Column('country', sa.String(), nullable=True),
        sa.Column('company_founded', sa.String(), nullable=True),
        sa.Column('industry_sector', sa.String(), nullable=True),
        sa.Column('sector_specification', sa.String(), nullable=True),
        sa.Column('sector_other', sa.String(), nullable=True),
        sa.Column('ownership', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('operating_currency', sa.String(), nullable=True),
        sa.Column('fiscal_year_start', sa.String(), nullable=True),
        sa.Column('employee_count', sa.String(), nullable=True),
        sa.Column('sources_internationally', sa.String(), nullable=True),
        sa.Column('source_countries', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('sells_internationally', sa.String(), nullable=True),
        sa.Column('sales_countries', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('topics_of_interest', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('consent_terms', sa.Boolean(), nullable=True),
        sa.Column('consent_data_processing', sa.Boolean(), nullable=True),
        sa.Column('consent_liability_waiver', sa.Boolean(), nullable=True),
        sa.Column('consent_photo_video', sa.Boolean(), nullable=True),
        sa.Column('cohort_date', sa.String(), nullable=True),
        sa.Column('wtc_location', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('tags', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            'responses', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column('crm_sync_status', sa.String(), nullable=False),
        sa.Column('crm_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('crm_contact_id', sa.String(), nullable=True),
        sa.Column('crm_error', sa.String(), nullable=True),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('opened_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clicked_materials', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('eligibility_status', sa.String(), nullable=False),
        sa.Column('eligibility_submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('owner_id', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(['trade_program_id'], ['trade_programs.id']),
        sa.ForeignKeyConstraint(['owner_id'], ['owners.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_trade_leads_program_email',
        'trade_leads',
        ['trade_program_id', 'email'],
        unique=True,
        postgresql_where=sa.text("email <> ''"),
    )
    op.create_index(
        'idx_trade_leads_registration', 'trade_leads', ['registration_id', 'participant_index']
    )
    op.create_index(
        'idx_trade_leads_program_created', 'trade_leads', ['trade_program_id', 'created_at']
    )
    op.create_index('idx_trade_leads_crm_sync', 'trade_leads', ['crm_sync_status'])

    op.add_column(
        'email_events', sa.Column('trade_lead_id', sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        'fk_email_events_trade_lead_id',
        'email_events',
        'trade_leads',
        ['trade_lead_id'],
        ['id'],
    )
    op.create_index('idx_email_events_trade_lead', 'email_events', ['trade_lead_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_email_events_trade_lead', table_name='email_events')
    op.drop_constraint('fk_email_events_trade_lead_id', 'email_events', type_='foreignkey')
    op.drop_column('email_events', 'trade_lead_id')

    op.drop_index('idx_trade_leads_crm_sync', table_name='trade_leads')
    op.drop_index('idx_trade_leads_program_created', table_name='trade_leads')
    op.drop_index('idx_trade_leads_registration', table_name='trade_leads')
    op.drop_index('idx_trade_leads_program_email', table_name='trade_leads')
    op.drop_table('trade_leads')

    op.drop_index('ix_trade_programs_slug', table_name='trade_programs')
    op.drop_table('trade_programs')
