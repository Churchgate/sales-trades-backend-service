from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import Field, SQLModel


class DashboardUser(SQLModel, table=True):
    __tablename__ = "dashboard_users"

    email: str = Field(primary_key=True)
    role: str  # 'superadmin' | 'gmd' | 'sales_manager' | 'rep'
    owner_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, ForeignKey("owners.id"))
    )
    hashed_password: str | None = Field(default=None)
    # Set when an admin invites a user with a temporary password; cleared once the
    # user sets their own. The frontend gates the dashboard on it.
    must_change_password: bool = Field(default=False)
