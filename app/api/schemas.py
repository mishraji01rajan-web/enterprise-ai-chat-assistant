from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.db.models import ApprovalStatus


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class ApprovalDecisionRequest(BaseModel):
    decision: str  # "approve" | "reject"


class ApprovalOut(BaseModel):
    id: str
    conversation_id: str
    requested_by: str
    tool_name: str
    summary: str
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None = None
    result_json: str | None = None

    model_config = {"from_attributes": True}
