from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskSnapshot

_UPSERTABLE_COLUMNS = [c.name for c in TaskSnapshot.__table__.columns if c.name != "task_id"]


async def upsert_task(session: AsyncSession, data: dict[str, Any]) -> None:
    """Insert or update a tasks_snapshot row. `data` must include `task_id`."""
    stmt = insert(TaskSnapshot).values(**data)
    update_cols = {
        col: getattr(stmt.excluded, col)
        for col in _UPSERTABLE_COLUMNS
        if col in data
    }
    stmt = stmt.on_conflict_do_update(index_elements=[TaskSnapshot.task_id], set_=update_cols)
    await session.execute(stmt)
