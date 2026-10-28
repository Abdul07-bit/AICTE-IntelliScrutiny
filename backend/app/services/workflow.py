"""Application workflow state machine.

There is deliberately no APPROVED/REJECTED state: the system prepares applications for human
scrutiny; formal decisions are taken outside this prototype by authorized officials."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.permissions import Role
from app.models import Application, ReviewAction, User
from app.services import audit

SYSTEM = "SYSTEM"
R = Role

STATES = ["DRAFT", "SUBMITTED", "PROCESSING", "VALIDATION_COMPLETE", "REVIEW_REQUIRED", "REVIEW_IN_PROGRESS",
          "RECTIFICATION_REQUESTED", "READY_FOR_FURTHER_SCRUTINY"]

TRANSITIONS: dict[str, dict[str, set]] = {
    "DRAFT": {"SUBMITTED": {R.INSTITUTION_UPLOADER, R.SCRUTINY_OFFICER}},
    "SUBMITTED": {"PROCESSING": {SYSTEM}},
    "PROCESSING": {"VALIDATION_COMPLETE": {SYSTEM}, "SUBMITTED": {SYSTEM}},
    "VALIDATION_COMPLETE": {"REVIEW_REQUIRED": {SYSTEM}, "REVIEW_IN_PROGRESS": {R.SCRUTINY_OFFICER},
                            "PROCESSING": {SYSTEM}},
    "REVIEW_REQUIRED": {"REVIEW_IN_PROGRESS": {R.SCRUTINY_OFFICER}, "PROCESSING": {SYSTEM}},
    "REVIEW_IN_PROGRESS": {"RECTIFICATION_REQUESTED": {R.SCRUTINY_OFFICER},
                           "READY_FOR_FURTHER_SCRUTINY": {R.SCRUTINY_OFFICER}, "PROCESSING": {SYSTEM}},
    "RECTIFICATION_REQUESTED": {"SUBMITTED": {R.INSTITUTION_UPLOADER, R.SCRUTINY_OFFICER}},
    "READY_FOR_FURTHER_SCRUTINY": {"REVIEW_IN_PROGRESS": {R.SCRUTINY_OFFICER}, "PROCESSING": {SYSTEM}},
}

STATE_LABELS = {
    "READY_FOR_FURTHER_SCRUTINY": "Ready for further scrutiny (not an approval)",
    "RECTIFICATION_REQUESTED": "Rectification requested from institution",
}


def allowed_next(app: Application, user: User | None) -> list[str]:
    actor = SYSTEM if user is None else Role(user.role)
    return [to for to, roles in TRANSITIONS.get(app.status, {}).items() if actor in roles]


def transition(db: Session, app: Application, to: str, user: User | None, note: str | None = None,
               action: str = "STATUS_CHANGE") -> Application:
    actor = SYSTEM if user is None else Role(user.role)
    roles = TRANSITIONS.get(app.status, {}).get(to)
    if roles is None:
        raise AppError(409, "INVALID_TRANSITION", f"Cannot move from {app.status} to {to}",
                       {"from": app.status, "to": to})
    if actor not in roles:
        raise AppError(403, "TRANSITION_NOT_PERMITTED", f"Role {actor} may not move an application to {to}")
    prev = app.status
    app.status = to
    if user is not None:
        db.add(ReviewAction(application_id=app.id, actor_id=user.id, action=action, note=note,
                            previous_status=prev, new_status=to))
    audit.record(db, "application.status_changed", "application", app.id, actor=user, application_id=app.id,
                 institution_id=app.institution_id, details={"from": prev, "to": to, "note": note})
    db.flush()
    return app
