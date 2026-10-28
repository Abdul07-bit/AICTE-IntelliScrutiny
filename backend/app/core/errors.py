"""Consistent API error envelope: {"error": {"code", "message", "request_id", "details"}}."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("intelliscrutiny.errors")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def not_found(what: str = "Resource") -> AppError:
    return AppError(404, "NOT_FOUND", f"{what} not found")


def forbidden(message: str = "You do not have permission to perform this action") -> AppError:
    return AppError(403, "FORBIDDEN", message)


def conflict(message: str, details: dict | None = None) -> AppError:
    return AppError(409, "CONFLICT", message, details)


def bad_request(message: str, details: dict | None = None) -> AppError:
    return AppError(400, "BAD_REQUEST", message, details)


def _envelope(request: Request, status: int, code: str, message: str, details=None) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": rid, "details": details or {}}},
        headers={"X-Request-ID": rid or ""},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _envelope(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
                413: "PAYLOAD_TOO_LARGE", 429: "RATE_LIMITED"}.get(exc.status_code, "HTTP_ERROR")
        return _envelope(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return _envelope(request, 422, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Never leak internals to clients; details go to the (redacted) server log.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _envelope(request, 500, "INTERNAL_ERROR", "An internal error occurred. Quote the request ID when reporting.")
