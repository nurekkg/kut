"""Ревизия a1efb2b3d84b: перед добавлением границ дат ГПР миграция считает строки с нарушением
(в обход RLS, включая удалённые) и останавливается с перечнем, ничего не меняя и не удаляя."""

import re
from collections.abc import AsyncIterator, Iterator
from datetime import date

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from tests.auth_support import AuthWorld
from tests.conftest import ALEMBIC_INI, require_test_url
from tests.gpr_support import make_plan, purge

BEFORE = "a3f7c1d9e582"
THIS = "a1efb2b3d84b"


@pytest.fixture(autouse=True)
async def cleanup(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[None]:
    yield
    await purge(worker_engine, auth_world)


@pytest.fixture
def alembic(test_database: None) -> Iterator[Config]:
    """Alembic против тестовой БД под kut_owner; после теста схема снова на head."""
    url = require_test_url("TEST_DATABASE_OWNER_URL").render_as_string(hide_password=False)
    config = Config(str(ALEMBIC_INI))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            yield config
        finally:
            command.upgrade(config, "head")
            get_settings.cache_clear()
    get_settings.cache_clear()


async def set_dates(engine: AsyncEngine, item: object, start: date, end: date) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE schedule_item SET plan_start = :s, plan_end = :e WHERE id = :id"),
            {"s": start, "e": end, "id": item},
        )


async def test_the_upgrade_refuses_rows_outside_the_limits_and_changes_nothing(
    alembic: Config, worker_engine: AsyncEngine, auth_world: AuthWorld
) -> None:
    plan = await make_plan(worker_engine, auth_world)
    command.downgrade(alembic, BEFORE)
    await set_dates(worker_engine, plan.item, date(1900, 1, 1), date(9999, 12, 31))
    async with worker_engine.begin() as connection:  # удалена мягко: на проверку это не влияет
        await connection.execute(
            text("UPDATE schedule_item SET deleted_at = now() WHERE id = :id"), {"id": plan.item}
        )

    with pytest.raises(RuntimeError) as refusal:
        command.upgrade(alembic, THIS)

    message = str(refusal.value)
    assert re.search(r"schedule_item: [1-9]\d* строк", message)
    assert str(plan.item) in message and "(удалена)" in message
    async with worker_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT plan_start, plan_end FROM schedule_item WHERE id = :id"),
                {"id": plan.item},
            )
        ).one()
        constraints = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conrelid = 'schedule_item'::regclass"
                " AND conname IN ('ck_schedule_item_dates_in_range',"
                " 'ck_schedule_item_duration_max')"
            )
        )
    assert row == (date(1900, 1, 1), date(9999, 12, 31))  # строку миграция не тронула
    assert constraints == 0  # и ограничений не добавила

    await set_dates(worker_engine, plan.item, date(2026, 9, 1), date(2026, 9, 10))
    command.upgrade(alembic, THIS)  # исправили данные - миграция проходит
