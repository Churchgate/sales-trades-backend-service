from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign


async def get_by_slug(session: AsyncSession, slug: str) -> Campaign | None:
    result = await session.execute(select(Campaign).where(Campaign.slug == slug))
    return result.scalars().first()


async def get(session: AsyncSession, campaign_id: int) -> Campaign | None:
    return await session.get(Campaign, campaign_id)


async def list_all(session: AsyncSession, *, status: str | None = None) -> list[Campaign]:
    stmt = select(Campaign)
    if status is not None:
        stmt = stmt.where(Campaign.status == status)
    result = await session.execute(stmt.order_by(Campaign.created_at.desc()))
    return list(result.scalars().all())


async def create(session: AsyncSession, campaign: Campaign) -> Campaign:
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def update(session: AsyncSession, campaign: Campaign) -> Campaign:
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return campaign
