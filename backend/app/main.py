from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.router import api_router
from app.api.routes.ws_quotes import quotes_ws
from app.core.config import get_settings
from app.core.logging import configure_logging

APP_VERSION = "0.1.0"


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    if not settings.app_auth_token:
        raise RuntimeError("APP_AUTH_TOKEN must be set — see .env.example")

    app = FastAPI(
        title="Plutus",
        version=APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    # live-quote websocket lives on the raw app: a websocket handshake can't run the
    # bearer Depends, so it validates its own ?token= against APP_AUTH_TOKEN.
    app.add_api_websocket_route("/ws/quotes", quotes_ws)

    @app.get("/health")
    def health():
        from app.core.db import get_engine
        from app.providers.registry import _shared_redis

        db_status = redis_status = "ok"
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_status = "error"
        try:
            _shared_redis().ping()
        except Exception:  # noqa: BLE001
            redis_status = "error"
        overall = "ok" if db_status == redis_status == "ok" else "degraded"
        return {"status": overall, "db": db_status, "redis": redis_status, "version": APP_VERSION}

    return app


app = create_app()
