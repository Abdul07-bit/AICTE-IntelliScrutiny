"""Password hashing, session tokens and short-lived signed file URLs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import timedelta

import jwt

from app.core.config import get_settings
from app.db.types import utcnow

_PBKDF2_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


def create_access_token(user_id: str, role: str) -> str:
    s = get_settings()
    now = utcnow()
    payload = {
        "sub": user_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.jwt_ttl_minutes)).timestamp()),
        "jti": secrets.token_hex(8),
        "typ": "session",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        return data if data.get("typ") == "session" else None
    except jwt.PyJWTError:
        return None


def sign_file_token(document_id: str, user_id: str, kind: str = "document") -> str:
    """Short-lived token bound to a specific object and user. The download route still
    re-checks authorization; the token only prevents URL reuse/sharing."""
    exp = int(time.time()) + get_settings().signed_url_ttl_seconds
    msg = f"{kind}:{document_id}:{user_id}:{exp}"
    sig = hmac.new(get_settings().jwt_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{msg}:{sig}".encode()).decode().rstrip("=")


def verify_file_token(token: str) -> tuple[str, str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        kind, object_id, user_id, exp, sig = raw.split(":")
        msg = f"{kind}:{object_id}:{user_id}:{exp}"
        expected = hmac.new(get_settings().jwt_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected) or int(exp) < time.time():
            return None
        return kind, object_id, user_id
    except Exception:
        return None
