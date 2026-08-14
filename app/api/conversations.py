"""Conversation history endpoints (multi-turn memory persisted in SQL)."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import ConversationDetailOut, ConversationOut
from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.db.models import Conversation, Message, Role
from app.db.session import session_dependency

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _assert_owner_or_admin(conversation: Conversation, user: CurrentUser) -> None:
    if conversation.user_id != user.id and user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your conversation")


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(session_dependency),
) -> Conversation:
    now = datetime.utcnow()
    conversation = Conversation(
        id=str(uuid.uuid4()), user_id=current_user.id, title="New conversation", created_at=now, updated_at=now
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(session_dependency),
) -> list[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(session_dependency),
) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    _assert_owner_or_admin(conversation, current_user)
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return ConversationDetailOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages,
    )
