"""add_rooms_and_bookings

Hall/boardroom booking system: bookable rooms and their confirmed bookings.
Bookings are public (no auth), confirmed instantly, and carry an emailed access code.

Revision ID: e7c2a4f9b6d1
Revises: d5f1a9c3b7e2
Create Date: 2026-06-22 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7c2a4f9b6d1'
down_revision: Union[str, Sequence[str], None] = 'd5f1a9c3b7e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rooms',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('location', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rooms_name', 'rooms', ['name'], unique=True)

    op.create_table(
        'bookings',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('room_id', sa.BigInteger(), nullable=False),
        sa.Column('booker_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('booker_email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('access_code', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_bookings_room_time', 'bookings', ['room_id', 'start_time', 'end_time'])
    op.create_index('idx_bookings_access_code', 'bookings', ['access_code'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_bookings_access_code', table_name='bookings')
    op.drop_index('idx_bookings_room_time', table_name='bookings')
    op.drop_table('bookings')
    op.drop_index('ix_rooms_name', table_name='rooms')
    op.drop_table('rooms')
