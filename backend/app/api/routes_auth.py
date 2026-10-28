from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.api.deps import current_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.middleware import SESSION_COOKIE
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.db.types import utcnow
from app.models import User
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        audit.record(db, "auth.login_failed", "user", user.id if user else None,
                     details={"email_domain": body.email.split("@")[-1][:60]})
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password")
    token = create_access_token(user.id, user.role)
    user.last_login_at = utcnow()
    audit.record(db, "auth.login", "user", user.id, actor=user)
    db.commit()
    s = get_settings()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=s.jwt_ttl_minutes * 60, path="/")
    return {"user": S.user(user, with_perms=True), "access_token": token, "token_type": "bearer"}


@router.post("/logout")
def logout(response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    audit.record(db, "auth.logout", "user", user.id, actor=user)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    s = get_settings()
    return {"user": S.user(user, with_perms=True), "environment": s.app_env,
            "synthetic_data": not s.is_production}
