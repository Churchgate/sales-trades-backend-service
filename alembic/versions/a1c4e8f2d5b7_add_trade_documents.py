"""add_trade_documents

Eligibility-document upload phase: companies submit CAC certificate, logo,
company profile, and/or business plan directly through wtcabuja.com (see
POST /trade/programs/{slug}/eligibility), matched to a registration by
`registration_id`. Documents are company-level (shared by both participants
of a registration), so they key off `registration_id` rather than a single
participant — hence a dedicated table instead of columns on `trade_leads`.
Files themselves live in Cloudflare R2; this table only stores the object
key and metadata.

Revision ID: a1c4e8f2d5b7
Revises: e3f5a7c9b1d2
Create Date: 2026-07-24 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c4e8f2d5b7'
down_revision: Union[str, Sequence[str], None] = 'e3f5a7c9b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'trade_documents',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_program_id', sa.BigInteger(), nullable=False),
        sa.Column('registration_id', sa.String(), nullable=False),
        sa.Column('document_key', sa.String(), nullable=False),
        sa.Column('storage_key', sa.String(), nullable=False),
        sa.Column('file_name', sa.String(), nullable=False),
        sa.Column('content_type', sa.String(), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['trade_program_id'], ['trade_programs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_trade_documents_registration_key',
        'trade_documents',
        ['registration_id', 'document_key'],
        unique=True,
    )
    op.create_index('idx_trade_documents_program', 'trade_documents', ['trade_program_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_trade_documents_program', table_name='trade_documents')
    op.drop_index('idx_trade_documents_registration_key', table_name='trade_documents')
    op.drop_table('trade_documents')
