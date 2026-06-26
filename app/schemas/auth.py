from pydantic import BaseModel, field_validator

VALID_ROLES = {"superadmin", "admin", "hod", "team_lead", "rep"}
MIN_PASSWORD_LENGTH = 8


def _validate_password_strength(v: str) -> str:
    if len(v) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return v


def _validate_churchgate_email(v: str) -> str:
    if not v.lower().endswith("@churchgate.com"):
        raise ValueError("email must be a @churchgate.com address")
    return v.lower()


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateUserRequest(BaseModel):
    """Invite a dashboard user. `password` is optional — when omitted a secure
    temporary password is generated and the user is forced to change it on first
    login."""

    email: str
    role: str
    owner_id: int | None = None
    password: str | None = None

    @field_validator("email")
    @classmethod
    def must_be_churchgate_email(cls, v: str) -> str:
        return _validate_churchgate_email(v)

    @field_validator("role")
    @classmethod
    def must_be_valid_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v

    @field_validator("password")
    @classmethod
    def password_is_strong(cls, v: str | None) -> str | None:
        return v if v is None else _validate_password_strength(v)


class ChangePasswordRequest(BaseModel):
    """A logged-in user replacing their (possibly temporary) password."""

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_is_strong(cls, v: str) -> str:
        return _validate_password_strength(v)


class CurrentUser(BaseModel):
    email: str
    role: str
    owner_id: int | None = None
    must_change_password: bool = False
