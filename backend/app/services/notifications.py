from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, User


def notify_roles(db: Session, roles: list[str], title: str, body: str, link: str | None = None,
                 severity: str = "INFO") -> None:
    for user in db.scalars(select(User).where(User.role.in_(roles), User.is_active.is_(True))):
        db.add(Notification(user_id=user.id, title=title, body=body, link=link, severity=severity))


def notify_user(db: Session, user_id: str, title: str, body: str, link: str | None = None,
                severity: str = "INFO") -> None:
    db.add(Notification(user_id=user_id, title=title, body=body, link=link, severity=severity))
