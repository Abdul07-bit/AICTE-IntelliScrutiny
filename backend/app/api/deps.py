from __future__ import annotations

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.errors import AppError, forbidden, not_found
from app.core.middleware import SESSION_COOKIE
from app.core.permissions import TENANT_SCOPED_ROLES, P, Role, permissions_for
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Application, Document, User
from app.services.audit import request_ctx


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif SESSION_COOKIE in request.cookies:
        token = request.cookies[SESSION_COOKIE]
    data = decode_access_token(token) if token else None
    if not data:
        raise AppError(401, "UNAUTHENTICATED", "Authentication required")
    user = db.get(User, data["sub"])
    if user is None or not user.is_active:
        raise AppError(401, "UNAUTHENTICATED", "Account is inactive or no longer exists")
    request_ctx.set({"actor_id": user.id, "actor_email": user.email, "actor_role": user.role,
                     "request_id": getattr(request.state, "request_id", None),
                     "ip": request.client.host if request.client else None})
    return user


def require(*perms: P):
    def dep(user: User = Depends(current_user)) -> User:
        granted = permissions_for(user.role)
        missing = [p for p in perms if p not in granted]
        if missing:
            raise forbidden(f"Missing permission: {', '.join(missing)}")
        return user
    return dep


def is_tenant_scoped(user: User) -> bool:
    return Role(user.role) in TENANT_SCOPED_ROLES


def load_application(db: Session, app_id: str, user: User) -> Application:
    """Object-level authorization: institution users only see their own institution's applications.
    Returns 404 (not 403) for other tenants' objects to avoid confirming existence."""
    app = db.get(Application, app_id)
    if app is None or (is_tenant_scoped(user) and app.institution_id != user.institution_id):
        raise not_found("Application")
    return app


def load_document(db: Session, doc_id: str, user: User) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None or (is_tenant_scoped(user) and doc.institution_id != user.institution_id):
        raise not_found("Document")
    return doc


class Page:
    def __init__(self, page: int = Query(1, ge=1, le=10_000), page_size: int = Query(25, ge=1, le=100)):
        self.page, self.page_size = page, page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def paginate(db: Session, stmt, page: Page, serialize) -> dict:
    from sqlalchemy import func, select

    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.offset(page.offset).limit(page.page_size)).all()
    return {"items": [serialize(r) for r in rows], "total": total, "page": page.page, "page_size": page.page_size}
