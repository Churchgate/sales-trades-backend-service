from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dashboard_user import DashboardUser


async def get_user_by_email(session: AsyncSession, email: str) -> DashboardUser | None:
    return await session.get(DashboardUser, email)


async def create_user(session: AsyncSession, user: DashboardUser) -> DashboardUser:
    session.add(user)
    await session.commit()
    return user


async def set_password(
    session: AsyncSession, user: DashboardUser, hashed: str, *, must_change: bool
) -> DashboardUser:
    user.hashed_password = hashed
    user.must_change_password = must_change
    session.add(user)
    await session.commit()
    return user


async def delete_user(session: AsyncSession, user: DashboardUser) -> None:
    await session.delete(user)
    await session.commit()


async def list_users(session: AsyncSession) -> list[DashboardUser]:
    result = await session.execute(select(DashboardUser))
    return list(result.scalars().all())
