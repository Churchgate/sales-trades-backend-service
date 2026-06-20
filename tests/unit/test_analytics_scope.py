import pytest
from fastapi import HTTPException

from app.api.dependencies import get_owner_scope
from app.models.dashboard_user import DashboardUser


def _user(role: str, owner_id: int | None) -> DashboardUser:
    return DashboardUser(email="u@x.com", role=role, owner_id=owner_id, hashed_password="x")


def test_rep_scoped_to_own_owner_id() -> None:
    assert get_owner_scope(_user("rep", 100)) == 100


def test_manager_roles_see_everything() -> None:
    for role in ("gmd", "sales_manager", "superadmin"):
        assert get_owner_scope(_user(role, None)) is None


def test_rep_without_owner_is_forbidden() -> None:
    with pytest.raises(HTTPException) as exc:
        get_owner_scope(_user("rep", None))
    assert exc.value.status_code == 403
