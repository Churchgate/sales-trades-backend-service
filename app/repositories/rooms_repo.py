from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.room import Room


async def list_active_rooms(session: AsyncSession) -> list[Room]:
    result = await session.execute(
        select(Room).where(Room.is_active.is_(True)).order_by(Room.name)
    )
    return list(result.scalars().all())


async def get_room(session: AsyncSession, room_id: int) -> Room | None:
    return await session.get(Room, room_id)


async def create_room(session: AsyncSession, room: Room) -> Room:
    session.add(room)
    await session.commit()
    await session.refresh(room)
    return room
