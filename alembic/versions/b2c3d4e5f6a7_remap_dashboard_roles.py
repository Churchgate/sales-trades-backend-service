"""remap dashboard user roles to superadmin/admin/hod/team_lead/rep

Data-only migration: the role set changed from
superadmin/gmd/sales_manager/rep to superadmin/admin/hod/team_lead/rep.
Map existing users so no one is left on a now-invalid role.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-26 12:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: str | Sequence[str] | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE dashboard_users SET role = 'admin' WHERE role = 'gmd'")
    op.execute("UPDATE dashboard_users SET role = 'team_lead' WHERE role = 'sales_manager'")


def downgrade() -> None:
    op.execute("UPDATE dashboard_users SET role = 'gmd' WHERE role = 'admin'")
    op.execute("UPDATE dashboard_users SET role = 'sales_manager' WHERE role = 'team_lead'")
