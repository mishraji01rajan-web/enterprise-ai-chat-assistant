"""Authentication endpoints: username/password login -> JWT."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser, TokenResponse
from app.auth.security import create_access_token, verify_password
from app.db.models import User
from app.db.session import session_dependency
from app.observability.logging_config import get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger("app.api.auth")


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(session_dependency)) -> TokenResponse:
    user = db.query(User).filter(User.username == form_data.username).one_or_none()
    if not user or not user.is_active or not verify_password(form_data.password, user.hashed_password):
        logger.warning("login_failed", username=form_data.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")

    token = create_access_token(subject=user.username, extra_claims={"role": user.role.value})
    logger.info("login_success", username=user.username, role=user.role.value)
    return TokenResponse(access_token=token, role=user.role, full_name=user.full_name, username=user.username)


@router.get("/me", response_model=CurrentUser)
def me(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return current_user
