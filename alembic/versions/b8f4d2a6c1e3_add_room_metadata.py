"""add_room_metadata

Room cards/detail in the booking frontend design need a type, size, amenity
list, and hero image per room — fields the original rooms table didn't have.

Revision ID: b8f4d2a6c1e3
Revises: e7c2a4f9b6d1
Create Date: 2026-06-22 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8f4d2a6c1e3'
down_revision: Union[str, Sequence[str], None] = 'e7c2a4f9b6d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('rooms', sa.Column('room_type', sa.String(), nullable=True))
    op.add_column('rooms', sa.Column('size_sqm', sa.Float(), nullable=True))
    op.add_column(
        'rooms', sa.Column('amenities', postgresql.ARRAY(sa.String()), nullable=True)
    )
    op.add_column('rooms', sa.Column('image_url', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('rooms', 'image_url')
    op.drop_column('rooms', 'amenities')
    op.drop_column('rooms', 'size_sqm')
    op.drop_column('rooms', 'room_type')
