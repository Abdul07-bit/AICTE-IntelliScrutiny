"""Request IDs, rate limiting, CSRF guard and security headers."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings

SESSION_COOKIE = "is_session"
CSRF_HEADER = "x-requested-with"


def _error(status: int, code: str, message: str, rid: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": rid, "details": {}}},
        headers={"X-Request-ID": rid},
    )


class SlidingWindowLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        s = get_settings()
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if incoming and len(incoming) <= 40 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex[:16]
        request.state.request_id = rid
        ip = request.client.host if request.client else "unknown"

        path = request.url.path
        if path.startswith(s.api_prefix):
            is_login = path.endswith("/auth/login")
            limit = s.login_rate_limit_per_minute if is_login else s.rate_limit_per_minute
            if not limiter.allow(f"{ip}:{'login' if is_login else 'api'}", limit):
                return _error(429, "RATE_LIMITED", "Too many requests. Please retry shortly.", rid)

            # CSRF: cookie-authenticated state-changing requests must carry a custom header,
            # which cross-site forms cannot set. Bearer-token clients are unaffected.
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and not is_login
                and SESSION_COOKIE in request.cookies
                and "authorization" not in request.headers
                and request.headers.get(CSRF_HEADER) != "IntelliScrutiny"
            ):
                return _error(403, "CSRF_REJECTED", "Missing anti-CSRF header", rid)

        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if s.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
