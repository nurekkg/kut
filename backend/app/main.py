from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from app.api import api_v1_router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.log import configure_logging
from app.core.redis import close_redis
from app.core.request_id import RequestIdMiddleware
from app.modules.auth.keys import validate_auth_config
from app.modules.health.router import router as health_router
from app.modules.notifications.adapters import validate_notification_config


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()
    await close_redis()


def create_app() -> FastAPI:
    configure_logging()
    validate_auth_config()
    validate_notification_config(get_settings())
    settings = get_settings()
    docs_enabled = settings.docs_enabled
    app = FastAPI(
        title="Kut ERP API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.add_middleware(
        GZipMiddleware,
        minimum_size=settings.gzip_minimum_bytes,
        compresslevel=settings.gzip_compresslevel,
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)
    app.include_router(api_v1_router)
    return app


app = create_app()
