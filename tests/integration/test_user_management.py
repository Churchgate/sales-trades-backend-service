"""Invite + password-change flows (admin invite, authenticated change-password,
admin reset). Driven over HTTP so role-gating and request validation are exercised
exactly as a real client hits them."""

import httpx
import pytest_asyncio

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.core.security import hash_password, verify_password
from app.main import create_app
from app.models.dashboard_user import DashboardUser
from app.repositories import users_repo


@pytest_asyncio.fixture
async def client_as(db_session):
    """Returns a factory: `await client_as(role)` yields an httpx client whose
    requests authenticate as a user with that role."""
    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session

    def _make(role: str = "superadmin", email: str = "admin@churchgate.com", user=None):
        # `user` lets a test authenticate as a real persisted row (with its real
        # password hash) for flows like change-password that read the stored hash;
        # otherwise a lightweight stub with the requested role is enough.
        authed = user or DashboardUser(
            email=email, role=role, owner_id=None, hashed_password="x"
        )
        app.dependency_overrides[get_current_user] = lambda: authed
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    return _make


async def test_invite_generates_temp_password_and_flags_change(client_as, db_session):
    async with client_as("superadmin") as c:
        res = await c.post(
            "/api/v1/admin/users",
            json={"email": "rep1@churchgate.com", "role": "rep"},
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["temp_password"]  # returned for the admin to pass on
    assert body["email_sent"] is False  # no SendGrid configured in tests
    assert body["user"]["must_change_password"] is True

    user = await users_repo.get_user_by_email(db_session, "rep1@churchgate.com")
    assert user is not None and user.must_change_password is True
    # the stored hash is for the generated temp password we handed back
    assert verify_password(body["temp_password"], user.hashed_password)


async def test_invite_accepts_explicit_password(client_as, db_session):
    async with client_as("superadmin") as c:
        res = await c.post(
            "/api/v1/admin/users",
            json={"email": "rep2@churchgate.com", "role": "rep", "password": "supplied-pass-123"},
        )
    assert res.status_code == 201, res.text
    assert res.json()["temp_password"] == "supplied-pass-123"


async def test_invite_rejects_short_password(client_as):
    async with client_as("superadmin") as c:
        res = await c.post(
            "/api/v1/admin/users",
            json={"email": "rep3@churchgate.com", "role": "rep", "password": "short"},
        )
    assert res.status_code == 422


async def test_invite_duplicate_is_conflict(client_as, db_session):
    await users_repo.create_user(
        db_session,
        DashboardUser(email="dupe@churchgate.com", role="rep", hashed_password="x"),
    )
    async with client_as("superadmin") as c:
        res = await c.post(
            "/api/v1/admin/users", json={"email": "dupe@churchgate.com", "role": "rep"}
        )
    assert res.status_code == 409


async def test_invite_requires_superadmin(client_as):
    async with client_as("admin") as c:
        res = await c.post(
            "/api/v1/admin/users", json={"email": "rep4@churchgate.com", "role": "rep"}
        )
    assert res.status_code == 403


async def test_change_password_success_clears_flag(client_as, db_session):
    user = await users_repo.create_user(
        db_session,
        DashboardUser(
            email="user@churchgate.com", role="rep",
            hashed_password=hash_password("temp-pass-123"), must_change_password=True,
        ),
    )
    async with client_as(user=user) as c:
        res = await c.post(
            "/api/v1/auth/change-password",
            json={"current_password": "temp-pass-123", "new_password": "brand-new-456"},
        )
    assert res.status_code == 200, res.text

    user = await users_repo.get_user_by_email(db_session, "user@churchgate.com")
    assert user.must_change_password is False
    assert verify_password("brand-new-456", user.hashed_password)


async def test_change_password_wrong_current_rejected(client_as, db_session):
    user = await users_repo.create_user(
        db_session,
        DashboardUser(
            email="user2@churchgate.com", role="rep",
            hashed_password=hash_password("real-pass-123"),
        ),
    )
    async with client_as(user=user) as c:
        res = await c.post(
            "/api/v1/auth/change-password",
            json={"current_password": "wrong-pass", "new_password": "brand-new-456"},
        )
    assert res.status_code == 400


async def test_change_password_must_differ(client_as, db_session):
    user = await users_repo.create_user(
        db_session,
        DashboardUser(
            email="user3@churchgate.com", role="rep",
            hashed_password=hash_password("same-pass-123"),
        ),
    )
    async with client_as(user=user) as c:
        res = await c.post(
            "/api/v1/auth/change-password",
            json={"current_password": "same-pass-123", "new_password": "same-pass-123"},
        )
    assert res.status_code == 400


async def test_admin_reset_password_issues_new_temp(client_as, db_session):
    await users_repo.create_user(
        db_session,
        DashboardUser(
            email="reset@churchgate.com", role="rep",
            hashed_password=hash_password("old-pass-123"), must_change_password=False,
        ),
    )
    async with client_as("superadmin") as c:
        res = await c.post("/api/v1/admin/users/reset@churchgate.com/reset-password")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["temp_password"]
    assert body["user"]["must_change_password"] is True

    user = await users_repo.get_user_by_email(db_session, "reset@churchgate.com")
    assert user.must_change_password is True
    assert verify_password(body["temp_password"], user.hashed_password)
    assert not verify_password("old-pass-123", user.hashed_password)


async def test_admin_reset_unknown_user_is_404(client_as):
    async with client_as("superadmin") as c:
        res = await c.post("/api/v1/admin/users/nobody@churchgate.com/reset-password")
    assert res.status_code == 404


async def test_delete_user_removes_them(client_as, db_session):
    # Works for a pending invite too (must_change_password=True).
    await users_repo.create_user(
        db_session,
        DashboardUser(email="gone@churchgate.com", role="rep", hashed_password="x",
                      must_change_password=True),
    )
    async with client_as("superadmin") as c:
        res = await c.delete("/api/v1/admin/users/gone@churchgate.com")
    assert res.status_code == 200, res.text
    assert await users_repo.get_user_by_email(db_session, "gone@churchgate.com") is None


async def test_delete_user_requires_superadmin(client_as, db_session):
    await users_repo.create_user(
        db_session,
        DashboardUser(email="keep@churchgate.com", role="rep", hashed_password="x"),
    )
    async with client_as("admin") as c:
        res = await c.delete("/api/v1/admin/users/keep@churchgate.com")
    assert res.status_code == 403


async def test_delete_unknown_user_is_404(client_as):
    async with client_as("superadmin") as c:
        res = await c.delete("/api/v1/admin/users/nobody@churchgate.com")
    assert res.status_code == 404


async def test_delete_self_is_rejected(client_as):
    # The stub superadmin authenticates as admin@churchgate.com.
    async with client_as("superadmin", email="admin@churchgate.com") as c:
        res = await c.delete("/api/v1/admin/users/admin@churchgate.com")
    assert res.status_code == 400


async def test_update_role_changes_role(client_as, db_session):
    await users_repo.create_user(
        db_session,
        DashboardUser(email="grow@churchgate.com", role="rep", hashed_password="x"),
    )
    async with client_as("superadmin") as c:
        res = await c.patch(
            "/api/v1/admin/users/grow@churchgate.com", json={"role": "admin"}
        )
    assert res.status_code == 200, res.text
    assert res.json()["user"]["role"] == "admin"
    user = await users_repo.get_user_by_email(db_session, "grow@churchgate.com")
    # a non-rep role carries no owner scoping
    assert user.role == "admin" and user.owner_id is None


async def test_update_role_rejects_invalid_role(client_as, db_session):
    await users_repo.create_user(
        db_session,
        DashboardUser(email="bad@churchgate.com", role="rep", hashed_password="x"),
    )
    async with client_as("superadmin") as c:
        res = await c.patch(
            "/api/v1/admin/users/bad@churchgate.com", json={"role": "wizard"}
        )
    assert res.status_code == 422


async def test_update_role_requires_superadmin(client_as, db_session):
    await users_repo.create_user(
        db_session,
        DashboardUser(email="keep2@churchgate.com", role="rep", hashed_password="x"),
    )
    async with client_as("admin") as c:
        res = await c.patch(
            "/api/v1/admin/users/keep2@churchgate.com", json={"role": "admin"}
        )
    assert res.status_code == 403


async def test_update_own_role_is_rejected(client_as):
    async with client_as("superadmin", email="admin@churchgate.com") as c:
        res = await c.patch(
            "/api/v1/admin/users/admin@churchgate.com", json={"role": "rep"}
        )
    assert res.status_code == 400


async def test_update_unknown_user_is_404(client_as):
    async with client_as("superadmin") as c:
        res = await c.patch(
            "/api/v1/admin/users/nobody@churchgate.com", json={"role": "admin"}
        )
    assert res.status_code == 404
