"""AICTE IntelliScrutiny API — independent demonstration prototype, not an official AICTE product."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import BACKEND_DIR, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware

log = logging.getLogger("intelliscrutiny")

DISCLAIMER = ("Independent demonstration prototype. Not affiliated with, endorsed by, or an official product of AICTE. "
              "All institutions, documents, issuers and regulatory text in the demo are synthetic. Findings are "
              "advisory and require human review; the system makes no approval or rejection decisions.")


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    s = get_settings()
    s.assert_safe_for_environment()
    import app.services.handlers  # noqa: F401 - registers job handlers
    from app.services import jobs

    run_migrations()
    n = jobs.recover_interrupted()
    if n:
        log.warning("Marked %s interrupted job(s) as FAILED", n)
    if s.seed_demo_data and not s.is_production:
        from app.demo.seed import run_seed

        if run_seed():
            log.info("Synthetic demonstration data seeded")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="AICTE IntelliScrutiny API", version=__version__, description=DISCLAIMER, lifespan=lifespan,
                  docs_url=f"{s.api_prefix}/docs", openapi_url=f"{s.api_prefix}/openapi.json", redoc_url=None)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-Requested-With", "Idempotency-Key", "X-Request-ID"])
    install_error_handlers(app)

    from app.api import routes_applications, routes_auth, routes_knowledge, routes_ops

    for r in (routes_auth.router, routes_applications.router, routes_knowledge.router, routes_ops.router):
        app.include_router(r, prefix=s.api_prefix)

    @app.get(f"{s.api_prefix}/health", tags=["meta"])
    def health():
        return {"status": "ok", "version": __version__, "environment": s.app_env, "disclaimer": DISCLAIMER}

    return app


app = create_app()
