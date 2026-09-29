from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import dispose_engine
from app.core.log import configure_logging
from app.modules.health.router import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Kut ERP API", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
