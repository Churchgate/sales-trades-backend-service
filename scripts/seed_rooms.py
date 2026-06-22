"""Seed (or update) bookable rooms. There's no admin UI, so rooms live here.

Idempotent: matches on name, updates details, leaves bookings untouched. Edit the
ROOMS list below to reflect the real halls/boardrooms, then run:

    uv run python scripts/seed_rooms.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import session_scope
from app.models.room import Room

# Edit to match the real venues.
ROOMS: list[dict[str, object]] = [
    {"name": "Main Hall", "location": "Ground Floor", "capacity": 200,
     "description": "Large event hall with stage and AV."},
    {"name": "Boardroom A", "location": "3rd Floor", "capacity": 12,
     "description": "Executive boardroom with video conferencing."},
    {"name": "Boardroom B", "location": "3rd Floor", "capacity": 8,
     "description": "Small meeting room."},
    {"name": "Training Room", "location": "2nd Floor", "capacity": 30,
     "description": "Flexible training/seminar space."},
]


async def seed_rooms() -> None:
    async with session_scope() as session:
        for spec in ROOMS:
            existing = (
                await session.execute(select(Room).where(Room.name == spec["name"]))
            ).scalars().first()
            if existing is None:
                session.add(Room(**spec, is_active=True))
                print(f"  + created room: {spec['name']}")
            else:
                existing.location = spec.get("location")  # type: ignore[assignment]
                existing.capacity = spec.get("capacity")  # type: ignore[assignment]
                existing.description = spec.get("description")  # type: ignore[assignment]
                existing.is_active = True
                print(f"  ~ updated room: {spec['name']}")
        await session.commit()
    print(f"\nSeeded {len(ROOMS)} rooms.")


if __name__ == "__main__":
    asyncio.run(seed_rooms())
