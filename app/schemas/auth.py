from pydantic import BaseModel, field_validator

VALID_ROLES = {"superadmin", "gmd", "sales_manager", "rep"}


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: str
    owner_id: int | None = None

    @field_validator("email")
    @classmethod
    def must_be_churchgate_email(cls, v: str) -> str:
        if not v.lower().endswith("@churchgate.com"):
            raise ValueError("email must be a @churchgate.com address")
        return v.lower()

    @field_validator("role")
    @classmethod
    def must_be_valid_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v


class CurrentUser(BaseModel):
    email: str
    role: str
    owner_id: int | None = None
