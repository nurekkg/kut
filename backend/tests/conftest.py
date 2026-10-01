import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.redis import get_redis
from app.main import create_app
from app.modules.auth import devseed
from app.modules.auth.security import hash_password
from app.modules.rbac.seed import load_spec, sync_rbac
from tests import support_routes as test_routes
from tests.auth_support import PASSWORD, TOTP_SECRET, AuthWorld
from tests.models import ScratchBase
from tests.support import World

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def require_test_url(variable: str) -> URL:
    """URL тестовой БД из окружения. Тесты сбрасывают схему, поэтому только *_test."""
    url = make_url(os.environ[variable])
    if not (url.database or "").endswith("_test"):
        pytest.exit(f"{variable} must point at a *_test database, got {url.database!r}")
    return url


def _dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
async def test_database() -> AsyncIterator[None]:
    if get_settings().app_env == "production":
        pytest.exit("refusing to reset databases with APP_ENV=production")
    require_test_url("DATABASE_URL")
    owner_url = require_test_url("TEST_DATABASE_OWNER_URL")
    require_test_url("TEST_DATABASE_WORKER_URL")
    await dispose_engine()

    # Права суперпользователя не нужны: kut_owner владеет тестовой БД и пересоздаёт схемы сам.
    with psycopg.connect(_dsn(owner_url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS app CASCADE")
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", owner_url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        engine = create_engine(owner_url)
        with engine.begin() as connection:
            sync_rbac(connection, load_spec())
        engine.dispose()
    get_settings.cache_clear()
    yield
    await dispose_engine()


@pytest.fixture(scope="session")
async def owner_engine(test_database: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(require_test_url("TEST_DATABASE_OWNER_URL"))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def owner_sync_engine(test_database: None) -> Iterator[Engine]:
    engine = create_engine(require_test_url("TEST_DATABASE_OWNER_URL"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
async def worker_engine(test_database: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(require_test_url("TEST_DATABASE_WORKER_URL"))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def world(worker_engine: AsyncEngine) -> World:
    """Две юрлица, два объекта и по пользователю на каждый класс доступа; заводит kut_worker."""
    ids = {
        name: uuid4()
        for name in [
            "le1",
            "le2",
            "p1",
            "p2",
            "p_deleted",
            "sub_a",
            "sub_b",
            "customer",
            "supplier",
            "owner_user",
            "foreman_user",
            "sub_a_user",
            "sub_b_user",
            "customer_user",
        ]
    }
    async with worker_engine.begin() as connection:
        roles = {
            row.code: row.id for row in await connection.execute(text("SELECT code, id FROM role"))
        }
        for key, name in (("le1", "ЮЛ-1"), ("le2", "ЮЛ-2")):
            await connection.execute(
                text("INSERT INTO legal_entity (id, name) VALUES (:id, :name)"),
                {"id": ids[key], "name": name},
            )
        for key, entity, name, deleted in (
            ("p1", "le1", "Объект 1", False),
            ("p2", "le2", "Объект 2", False),
            ("p_deleted", "le1", "Удалённый объект", True),
        ):
            await connection.execute(
                text(
                    "INSERT INTO project (id, legal_entity_id, name, deleted_at)"
                    " VALUES (:id, :le, :name, CASE WHEN :deleted THEN now() END)"
                ),
                {"id": ids[key], "le": ids[entity], "name": name, "deleted": deleted},
            )
        for key, kind in (
            ("sub_a", "subcontractor"),
            ("sub_b", "subcontractor"),
            ("customer", "customer"),
            ("supplier", "supplier"),
        ):
            await connection.execute(
                text("INSERT INTO counterparty (id, name, types) VALUES (:id, :name, :types)"),
                {"id": ids[key], "name": f"Контрагент {key}", "types": [kind]},
            )
        users = (
            ("owner_user", "owner", None, "+996555000001"),
            ("foreman_user", "foreman", None, "+996555000002"),
            ("sub_a_user", "subcontractor", "sub_a", "+996555000003"),
            ("sub_b_user", "subcontractor", "sub_b", "+996555000004"),
            ("customer_user", "customer", "customer", "+996555000005"),
        )
        for key, role, counterparty, phone in users:
            await connection.execute(
                text(
                    'INSERT INTO "user" (id, phone, full_name, role_id, counterparty_id)'
                    " VALUES (:id, :phone, :name, :role, :counterparty)"
                ),
                {
                    "id": ids[key],
                    "phone": phone,
                    "name": key,
                    "role": roles[role],
                    "counterparty": ids[counterparty] if counterparty else None,
                },
            )
        access = (
            ("owner_user", "p1"),
            ("owner_user", "p2"),
            ("foreman_user", "p1"),
            ("foreman_user", "p_deleted"),
            ("sub_a_user", "p1"),
            ("sub_a_user", "p_deleted"),
            ("sub_b_user", "p1"),
            ("sub_b_user", "p2"),
            ("customer_user", "p1"),
        )
        for user, project in access:
            await connection.execute(
                text("INSERT INTO user_project_access (user_id, project_id) VALUES (:u, :p)"),
                {"u": ids[user], "p": ids[project]},
            )
        for entity in ("le1", "le2"):
            await connection.execute(
                text(
                    "INSERT INTO user_legal_entity_access (user_id, legal_entity_id)"
                    " VALUES (:u, :e)"
                ),
                {"u": ids["owner_user"], "e": ids[entity]},
            )
    return World(roles=roles, **ids)


@pytest.fixture
async def session(test_database: None, owner_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with owner_engine.connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(ScratchBase.metadata.create_all)
        async with AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db_session:
            yield db_session
        await transaction.rollback()


@pytest.fixture
def app(test_database: None) -> FastAPI:
    application = create_app()
    application.include_router(test_routes.router)  # только тестовое приложение
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


@pytest.fixture(scope="session")
def worker_sync_engine(test_database: None) -> Iterator[Engine]:
    engine = create_engine(require_test_url("TEST_DATABASE_WORKER_URL"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def auth_world(worker_sync_engine: Engine) -> AuthWorld:
    """Демо-данные devseed (14 пользователей, 2 объекта) с известными паролем и TOTP."""
    with worker_sync_engine.begin() as connection:
        devseed.seed(
            connection,
            password_hash=hash_password(PASSWORD),
            totp_secret=TOTP_SECRET,
            # В dev бухгалтер без 2FA (так проверяется настройка при входе), но тестам HTTP-матрицы
            # нужен готовый сеанс, поэтому здесь 2FA есть и у него. Настройку проверяют одноразовые.
            totp_roles=devseed.TOTP_ROLES | {"accountant"},
        )
        users = {
            row.email.split("@")[0]: row.id
            for row in connection.execute(
                text('SELECT id, email FROM "user" WHERE email LIKE :pattern'),
                {"pattern": f"%@{devseed.EMAIL_DOMAIN}"},
            )
        }
        projects = {
            key: connection.execute(
                text("SELECT id FROM project WHERE name = :name"), {"name": name}
            ).scalar_one()
            for key, name in (("east", devseed.EAST), ("west", devseed.WEST))
        }
        counterparties = {
            name: connection.execute(
                text("SELECT id FROM counterparty WHERE name = :name"), {"name": name}
            ).scalar_one()
            for name, _kind in devseed.COUNTERPARTIES
        }
        legal_entities = {
            name: connection.execute(
                text("SELECT id FROM legal_entity WHERE name = :name"), {"name": name}
            ).scalar_one()
            for name in devseed.LEGAL_ENTITIES
        }
        roles = {row.code: row.id for row in connection.execute(text("SELECT code, id FROM role"))}
    return AuthWorld(
        users=users,
        projects=projects,
        counterparties=counterparties,
        legal_entities=legal_entities,
        roles=roles,
    )


@pytest.fixture
async def clean_redis() -> AsyncIterator[None]:
    """Лимиты попыток, denylist и кэш прав живут в Redis: каждый тест начинает с чистой БД Redis."""
    if get_settings().app_env != "test":
        pytest.exit("refusing to flush Redis outside APP_ENV=test")
    await get_redis().flushdb()
    yield


@pytest.fixture
async def api(app: FastAPI, clean_redis: None, auth_world: AuthWorld) -> AsyncIterator[AsyncClient]:
    # https: cookie с флагом Secure отправляется клиентом только по защищённой схеме.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        yield client


@pytest.fixture
def new_user(
    worker_sync_engine: Engine, auth_world: AuthWorld
) -> Iterator[Callable[..., tuple[UUID, str]]]:
    """Одноразовый пользователь без объектов: (id, email), пароль PASSWORD; потом удаляется."""
    created: list[UUID] = []
    password_hash = hash_password(PASSWORD)

    def make(role: str = "foreman") -> tuple[UUID, str]:
        email = f"tmp-{uuid4().hex[:12]}@kut.test"
        with worker_sync_engine.begin() as connection:
            user_id = connection.execute(
                text(
                    'INSERT INTO "user" (email, full_name, role_id, password_hash)'
                    " VALUES (:email, :name, :role, :hash) RETURNING id"
                ),
                {
                    "email": email,
                    "name": f"Временный {role}",
                    "role": auth_world.roles[role],
                    "hash": password_hash,
                },
            ).scalar_one()
        created.append(user_id)
        return user_id, email

    yield make
    if created:
        with worker_sync_engine.begin() as connection:
            connection.execute(
                text('UPDATE "user" SET deleted_at = now() WHERE id = ANY(:ids)'), {"ids": created}
            )


@pytest.fixture
def settings_cleanup(worker_sync_engine: Engine) -> Iterator[None]:
    """Записи тестов настроек (@cfg.test, CFG-...) гасятся мягко: в чужие списки не попадут."""
    yield
    statements = (
        "UPDATE user_project_access SET deleted_at = now() WHERE deleted_at IS NULL"
        " AND user_id IN (SELECT id FROM \"user\" WHERE email LIKE '%@cfg.test')",
        "UPDATE user_legal_entity_access SET deleted_at = now() WHERE deleted_at IS NULL"
        " AND user_id IN (SELECT id FROM \"user\" WHERE email LIKE '%@cfg.test')",
        'UPDATE "user" SET deleted_at = now() WHERE deleted_at IS NULL'
        " AND email LIKE '%@cfg.test'",
        "UPDATE legal_entity SET deleted_at = now() WHERE deleted_at IS NULL AND name LIKE 'CFG-%'",
        "UPDATE counterparty SET deleted_at = now() WHERE deleted_at IS NULL AND name LIKE 'CFG-%'",
    )
    with worker_sync_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
