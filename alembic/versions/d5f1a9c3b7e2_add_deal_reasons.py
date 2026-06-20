"""add_deal_reasons

Lost/won deal-reason lookup (id -> name) synced from the Freshsales
/selector/deal_reasons selector; deals_snapshot.lost_reason_id references it.

Revision ID: d5f1a9c3b7e2
Revises: c4e7a1b9d2f3
Create Date: 2026-06-19 22:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5f1a9c3b7e2'
down_revision: Union[str, Sequence[str], None] = 'c4e7a1b9d2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'deal_reasons',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('deal_reasons')
