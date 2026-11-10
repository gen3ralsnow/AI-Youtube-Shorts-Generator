"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__, db
from ..config import get_settings
from ..logs import configure_logging, get_logger
from .deps import limiter
from .routes import assets, content, control, distribution

log = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    settings = get_settings()
    settings.ensure_dirs()
    if settings.database_url.startswith("sqlite"):
        db.create_all()  # dev convenience; PostgreSQL uses Alembic migrations
    if not settings.admin_api_token:
        log.warning("ADMIN_API_TOKEN is not set: every authenticated endpoint will refuse requests")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="GTA VI Autonomous Media Engine", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if not settings.is_production else None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.dashboard_origin], allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        client = request.client.host if request.client else "unknown"
        if request.url.path.startswith("/api/") and not limiter().allow(client):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    def liveness():
        return {"status": "ok", "version": __version__}

    for module in (content, assets, distribution, control):
        app.include_router(module.router)
    return app


app = create_app()
