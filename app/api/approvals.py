"""Human-approval endpoints for data-modifying tool calls.

Design: when the agent wants to run a write tool (currently only
`create_support_ticket`), `tool_node` stops short of executing it and
instead writes a `PendingApproval` row and tells the user what it proposes
to do. Nothing happens until a human calls this endpoint with an explicit
decision — the agent graph itself never resumes or re-executes anything.
This keeps the approval flow simple and easy to reason about (a plain
request/response, not graph-interrupt/resume machinery) while still fully
satisfying "no data-modifying action without explicit human confirmation".
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import ApprovalDecisionRequest, ApprovalOut
from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.db.models import ApprovalStatus, PendingApproval, Role
from app.db.session import session_dependency
from app.observability.logging_config import get_logger
from app.tools.executor import execute_tool

router = APIRouter(prefix="/approvals", tags=["approvals"])
logger = get_logger("app.api.approvals")


def _assert_can_decide(approval: PendingApproval, user: CurrentUser) -> None:
    if user.role == Role.ADMIN:
        return
    if approval.requested_by == user.username:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot decide on someone else's pending action")


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status_filter: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(session_dependency),
) -> list[PendingApproval]:
    query = db.query(PendingApproval)
    if current_user.role != Role.ADMIN:
        query = query.filter(PendingApproval.requested_by == current_user.username)
    if status_filter:
        try:
            query = query.filter(PendingApproval.status == ApprovalStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid status '{status_filter}'")
    return query.order_by(PendingApproval.created_at.desc()).all()


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(
    approval_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(session_dependency),
) -> PendingApproval:
    approval = db.get(PendingApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    _assert_can_decide(approval, current_user)
    return approval


@router.post("/{approval_id}/decide", response_model=ApprovalOut)
def decide_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(session_dependency),
) -> PendingApproval:
    approval = db.get(PendingApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    _assert_can_decide(approval, current_user)

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Approval already {approval.status.value}")

    decision = body.decision.strip().lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="decision must be 'approve' or 'reject'")

    if decision == "reject":
        approval.status = ApprovalStatus.REJECTED
        approval.decided_at = datetime.utcnow()
        db.commit()
        db.refresh(approval)
        logger.info("approval_rejected", approval_id=approval_id, decided_by=current_user.username)
        return approval

    args = json.loads(approval.tool_args_json)
    result = execute_tool(db, current_user, approval.tool_name, args, bypass_approval_gate=True)

    if not result.ok:
        approval.status = ApprovalStatus.REJECTED
        approval.result_json = json.dumps({"error": result.error})
        approval.decided_at = datetime.utcnow()
        db.commit()
        db.refresh(approval)
        logger.error("approval_execution_failed", approval_id=approval_id, error=result.error)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Approved action failed to execute: {result.error}")

    approval.status = ApprovalStatus.EXECUTED
    approval.result_json = json.dumps(result.data, default=str)
    approval.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(approval)
    logger.info("approval_executed", approval_id=approval_id, decided_by=current_user.username, tool=approval.tool_name)
    return approval
