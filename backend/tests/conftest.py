from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.main import create_app
from tests.models import ScratchBase

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture(scope="session")
async def test_database() -> AsyncIterator[None]:
    if get_settings().app_env == "production":
        pytest.exit("refusing to create databases with APP_ENV=production")
    await dispose_engine()
    source = make_url(get_settings().database_url.get_secret_value())
    test_name = f"{source.database}_test"
    admin_dsn = source.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    test_url = source.set(database=test_name).render_as_string(hide_password=False)

    def recreate_database(create: bool) -> None:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(test_name))
            )
            if create:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_name)))

    recreate_database(create=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", test_url)
        get_settings.cache_clear()
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        yield
        await dispose_engine()
    get_settings.cache_clear()
    recreate_database(create=False)


@pytest.fixture
async def session(test_database: None) -> AsyncIterator[AsyncSession]:
    async with get_engine().connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(ScratchBase.metadata.create_all)
        async with AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db_session:
            yield db_session
        await transaction.rollback()


@pytest.fixture
def app(test_database: None) -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client
