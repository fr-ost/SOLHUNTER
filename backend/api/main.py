"""FastAPI application factory for the SolHunter API service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import __version__
from backend.api.middleware import install_middleware
from backend.api.routers import health
from backend.config.logging import configure_logging, get_logger
from backend.config.settings import get_settings
from backend.database.engine import dispose_engine, get_engine
from backend.database.migrate import run_migrations_with_lock, wait_for_schema
from backend.services.redis_client import close_redis

log = get_logger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


async def _prepare_database() -> None:
    settings = get_settings()
    engine = get_engine()
    if settings.run_migrations_on_start:
        await run_migrations_with_lock(engine, settings.sqlalchemy_database_url)
    else:
        ok = await wait_for_schema(engine, settings.migration_wait_timeout_seconds)
        if not ok and settings.is_production:
            log.error("startup.schema_mismatch", hint="set RUN_MIGRATIONS_ON_START=true on exactly one service")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format, service="api")
    for problem in settings.validate_for_production():
        log.warning("startup.config_warning", problem=problem)
    log.info("api.starting", version=__version__, env=settings.app_env, port=settings.port)
    try:
        await _prepare_database()
    except Exception as exc:  # noqa: BLE001
        # Keep serving /health so the platform can report the failure instead of crash-looping silently.
        log.error("startup.database_unavailable", error=str(exc)[:300])
    finally:
        configure_logging(settings.log_level, settings.log_format, service="api")  # insurance against handler resets
    from backend.api.live import live_hub

    hub_task = asyncio.create_task(live_hub.run(), name="live-hub")
    worker = None
    worker_task = None
    if settings.service_role == "all":
        # Combined mode: the scanner runs inside this process (single Railway service).
        from backend.scanner.main import ScannerWorker

        worker = ScannerWorker(embedded=True)
        app.state.scanner = worker
        worker_task = asyncio.create_task(worker.run_embedded(), name="scanner")
        log.info("api.embedded_scanner_started")
    try:
        yield
    finally:
        if worker is not None:
            worker.request_stop()
            if worker_task is not None:
                try:
                    await asyncio.wait_for(worker_task, timeout=25)
                except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                    worker_task.cancel()
        hub_task.cancel()
        try:
            await hub_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await dispose_engine()
        await close_redis()
        log.info("api.stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = not settings.is_production
    app = FastAPI(
        title="SolHunter API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    origins = settings.allowed_origins or (["*"] if not settings.is_production else [])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
    )
    install_middleware(app)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
            for e in exc.errors()
        ]
        message = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:]) or 'body'}: {e['msg']}" for e in errors)[:300]
        return JSONResponse({"detail": message or "Validation error", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("http.unhandled", error=type(exc).__name__)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    app.include_router(health.router)
    from backend.api.routers import register_routers

    register_routers(app)

    if (WEB_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def dashboard_index() -> FileResponse:
            return FileResponse(WEB_DIST / "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        async def dashboard_spa(path: str) -> FileResponse:
            # Hash-routed single-page app: any non-API path serves the dashboard shell or a static file.
            if path.startswith(("api/", "ws", "health", "ready")):
                raise StarletteHTTPException(404, "Not found")
            candidate = WEB_DIST / path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {"service": "solhunter", "version": __version__, "dashboard": "not built (run `npm run build` in web/)", "docs": "/docs" if docs_enabled else "disabled"}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.api.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
        access_log=False,
    )


if __name__ == "__main__":
    run()
