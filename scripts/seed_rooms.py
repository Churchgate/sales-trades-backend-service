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

# Matches the WTC Abuja booking design (design_handoff_wtc_room_booking/README.md).
ROOMS: list[dict[str, object]] = [
    {
        "name": "Executive Boardroom", "location": "12th Floor", "capacity": 20,
        "description": "Executive boardroom with video conferencing.",
        "room_type": "Boardroom", "size_sqm": 85,
        "amenities": [
            "Projector", "Video Conferencing", "Whiteboard", "AC", "Wi-Fi",
            "Catering Available",
        ],
        "image_url": "https://images.unsplash.com/photo-1497366216548-37526070297c",
    },
    {
        "name": "Conference Hall A", "location": "5th Floor", "capacity": 40,
        "description": "Large conference hall with stage and PA system.",
        "room_type": "Conference Hall", "size_sqm": 140,
        "amenities": [
            "Dual Projectors", "PA System", "Stage", "AC", "Wi-Fi",
            "Recording Equipment",
        ],
        "image_url": "https://images.unsplash.com/photo-1540575467063-178a50c2df87",
    },
    {
        "name": "Breakout Room B", "location": "3rd Floor", "capacity": 8,
        "description": "Small meeting room for focused discussions.",
        "room_type": "Meeting Room", "size_sqm": 32,
        "amenities": ["Smart TV", "Whiteboard", "Video Call Ready", "AC", "Wi-Fi"],
        "image_url": "https://images.unsplash.com/photo-1497366754035-f200968a6e72",
    },
]

_FIELDS = (
    "location", "capacity", "description", "room_type", "size_sqm", "amenities",
    "image_url",
)


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
                for field in _FIELDS:
                    setattr(existing, field, spec.get(field))
                existing.is_active = True
                print(f"  ~ updated room: {spec['name']}")
        await session.commit()
    print(f"\nSeeded {len(ROOMS)} rooms.")


if __name__ == "__main__":
    asyncio.run(seed_rooms())
