from __future__ import annotations

from pydantic import BaseModel

from app.db.models import Role


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    full_name: str
    username: str


class CurrentUser(BaseModel):
    id: int
    username: str
    full_name: str
    role: Role
    customer_id: int | None = None

    model_config = {"from_attributes": True}
