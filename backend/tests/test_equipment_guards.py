"""Защита без GUC (SE-2b, находка b ревью): ключ сессии не открывает версии листа, переход ремонта
в done и прямой вызов определителя от приложения. Отказ приходит от привилегий колонок и функций
или от триггера, а не от наличия ключа в сессии."""

from collections.abc import Sequence
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.context import RequestContext, use_context
from app.core.db import get_sessionmaker
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    context,
    east,
    headers_for,
    idem,
    le_east,
    move,
    own_operator,
    own_unit,
    past,
    post_shift,
    shift_body,
)


async def app_steps(ctx: RequestContext, steps: Sequence[tuple[str, dict[str, Any]]]) -> None:
    """Запросы от имени приложения (kut_app) в одной сессии: настройка и запрос видят друг друга."""
    with use_context(ctx):
        async with get_sessionmaker()() as session:
            for sql, params in steps:
                await session.execute(text(sql), params)
            await session.commit()


async def confirmed_shift(api: AsyncClient, auth_world: AuthWorld) -> str:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    foreman = await headers_for(api, "foreman")
    operator = await own_operator(api, await headers_for(api, "machinist"))
    created = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(1),
            operator_id=operator,
        ),
    )
    assert created.status_code == 201, created.text
    shift_id: str = created.json()["id"]
    assert (await move(api, foreman, shift_id, "submit")).status_code == 200
    techdir = await headers_for(api, "techdir")
    assert (await move(api, techdir, shift_id, "confirm")).status_code == 200
    return shift_id


async def test_the_old_setting_does_not_let_the_app_move_the_posting_version(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    shift_id = await confirmed_shift(api, auth_world)
    ctx = context(auth_world, "techdir", (east(auth_world),), (le_east(auth_world),))
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_steps(
            ctx,
            [
                ("SELECT set_config('kut.posting', 'on', false)", {}),
                (
                    "UPDATE equipment_shift SET posting_version = 0 WHERE id = CAST(:id AS uuid)",
                    {"id": shift_id},
                ),
            ],
        )


async def test_the_app_cannot_touch_the_line_revision_or_call_the_posting_function(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    shift_id = await confirmed_shift(api, auth_world)
    ctx = context(auth_world, "techdir", (east(auth_world),), (le_east(auth_world),))
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_steps(
            ctx,
            [
                (
                    "UPDATE equipment_shift SET lines_revision = lines_revision + 1"
                    " WHERE id = CAST(:id AS uuid)",
                    {"id": shift_id},
                )
            ],
        )
    with pytest.raises(DBAPIError, match="permission denied for function equipment_shift_post"):
        await app_steps(
            ctx, [("SELECT app.equipment_shift_post(CAST(:id AS uuid))", {"id": shift_id})]
        )


async def test_the_repair_is_not_marked_done_by_the_app_even_with_the_old_setting(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    mechanic = await headers_for(api, "mechanic")
    created = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": unit["id"],
            "kind": "repair",
            "title": "Гидравлика",
            "planned_from": past(2),
        },
        headers={**mechanic, **idem()},
    )
    assert created.status_code == 201, created.text
    repair = created.json()["id"]
    started = await api.patch(
        f"{API}/maintenance/{repair}", json={"status": "in_progress"}, headers=mechanic
    )
    assert started.status_code == 200, started.text
    ctx = context(auth_world, "mechanic", (east(auth_world),), (le_east(auth_world),))
    with pytest.raises(DBAPIError, match=r"maintenance\.close_forbidden"):
        await app_steps(
            ctx,
            [
                ("SELECT set_config('kut.maintenance_close', 'on', false)", {}),
                (
                    "UPDATE equipment_maintenance SET status = 'done', done_at = now()"
                    " WHERE id = CAST(:id AS uuid)",
                    {"id": repair},
                ),
            ],
        )
