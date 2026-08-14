"""FastAPI auth dependencies: bearer-token extraction, current-user resolution,
and role-based access control (RBAC) guards.

These are enforced by the backend on every request — the LLM/agent never
decides who is authorized to do what. Endpoints and tools call
`require_roles(...)` themselves; nothing about authorization lives in a
prompt.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.auth.security import TokenError, decode_access_token
from app.db.models import Role, User
from app.db.session import session_dependency

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(session_dependency),
) -> CurrentUser:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_error
    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        raise credentials_error from exc

    username = payload.get("sub")
    if not username:
        raise credentials_error

    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None or not user.is_active:
        raise credentials_error

    return CurrentUser.model_validate(user)


def require_roles(*allowed_roles: Role):
    def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' is not permitted to perform this action.",
            )
        return current_user

    return _dependency
