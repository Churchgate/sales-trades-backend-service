from pydantic import BaseModel, field_validator

VALID_ROLES = {"superadmin", "admin", "hod", "team_lead", "rep"}
MIN_PASSWORD_LENGTH = 8
# Churchgate staff use @churchgate.com; Trade programs (Export Launchpad) are
# run out of WTC Abuja, whose staff use @wtcabuja.com — both get dashboard access.
ALLOWED_EMAIL_DOMAINS = ("@churchgate.com", "@wtcabuja.com")


def _validate_password_strength(v: str) -> str:
    if len(v) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return v


def _validate_churchgate_email(v: str) -> str:
    if not v.lower().endswith(ALLOWED_EMAIL_DOMAINS):
        raise ValueError(f"email must be one of: {', '.join(ALLOWED_EMAIL_DOMAINS)}")
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


class UpdateUserRequest(BaseModel):
    """Superadmin changing an existing user's role (and, for reps, their linked
    owner). `owner_id` only applies when the new role is `rep`; it's cleared for
    every other role, which see all deals."""

    role: str
    owner_id: int | None = None

    @field_validator("role")
    @classmethod
    def must_be_valid_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v


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
