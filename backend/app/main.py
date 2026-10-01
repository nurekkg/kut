from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_v1_router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.log import configure_logging
from app.modules.health.router import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    docs_enabled = get_settings().docs_enabled
    app = FastAPI(
        title="Kut ERP API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.include_router(health_router)
    app.include_router(api_v1_router)
    return app


app = create_app()
