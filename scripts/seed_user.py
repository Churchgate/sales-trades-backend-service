"""Seed (or update) a dashboard_users row for local dev / bootstrapping superadmin.

Prompts for a password, hashes it, and prints a ready-to-use access-token
cookie so protected routes can be exercised without going through the login flow.

Usage:
    uv run python scripts/seed_user.py <email> <role> [owner_id]

    role: superadmin | gmd | sales_manager | rep
"""

import asyncio
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import session_scope
from app.core.security import create_access_token, hash_password
from app.models.dashboard_user import DashboardUser

VALID_ROLES = {"superadmin", "gmd", "sales_manager", "rep"}


async def seed_user(email: str, role: str, password: str, owner_id: int | None) -> None:
    async with session_scope() as session:
        user = await session.get(DashboardUser, email)
        if user is None:
            user = DashboardUser(
                email=email, role=role, owner_id=owner_id,
                hashed_password=hash_password(password),
            )
            session.add(user)
        else:
            user.role = role
            user.owner_id = owner_id
            user.hashed_password = hash_password(password)
        await session.commit()

    token = create_access_token(email, role)
    print(f"\nSeeded dashboard_users: {email} ({role})")
    print("\nFor local testing, set this cookie on requests:")
    print(f"  access_token={token}")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    email, role = sys.argv[1], sys.argv[2]
    if role not in VALID_ROLES:
        print(f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}")
        sys.exit(1)

    owner_id = int(sys.argv[3]) if len(sys.argv) > 3 else None

    password = getpass("Password: ")
    if not password:
        print("Password cannot be empty.")
        sys.exit(1)

    asyncio.run(seed_user(email, role, password, owner_id))


if __name__ == "__main__":
    main()
