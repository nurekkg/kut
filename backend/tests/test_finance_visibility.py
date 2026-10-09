"""S3a, хвосты S2: бюджет, заказчик, адрес, координаты и гарантия объекта - только ролям с
finance:read (решение PO, tz-changes §3); граница стоит в БД (RLS таблицы project_finance), а не
только в ответе API. Здесь же: путь узла (project_node.path) для логинов приложения закрыт."""

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.rules import rule_error
from app.modules.auth import devseed
from app.modules.auth.principal import Principal
from app.modules.projects.router import _require_finance_for
from tests.auth_support import AuthWorld, bearer, token_for
from tests.db_support import call, owner_rows
from tests.gpr_support import ctx

P = "/api/v1/projects"

# Есть доступ к «Востоку» и право finance:read (строка «Финансы» матрицы §7.3).
READERS = ["owner", "finance", "techdir", "foreman", "lawyer"]
# Есть доступ к «Востоку», но finance:read нет: мастер «—», сметчик «—», кладовщик «—».
BLIND = ["master", "estimator", "storekeeper"]
EXTERNAL = ["customer", "sub_a"]

COMMERCIAL = (
    "planned_budget",
    "budget_currency",
    "customer_id",
    "customer_name",
    "address",
    "latitude",
    "longitude",
    "warranty_until",
)


@pytest.fixture
def finance_cleanup(worker_sync_engine: Engine) -> Iterator[None]:
    yield
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE project_node SET deleted_at = now() WHERE deleted_at IS NULL AND project_id"
                " IN (SELECT id FROM project WHERE name LIKE 'FIN-%')"
            )
        )
        connection.execute(
            text(
                "UPDATE project SET deleted_at = now()"
                " WHERE deleted_at IS NULL AND name LIKE 'FIN-%'"
            )
        )


async def card(api: AsyncClient, who: str, project: Any) -> dict[str, Any]:
    response = await api.get(f"{P}/{project}", headers=bearer(await token_for(api, who)))
    assert response.status_code == 200, (who, response.text)
    body: dict[str, Any] = response.json()
    return body


# --- API: карточка ----------------------------------------------------------------------------


@pytest.mark.parametrize("who", READERS)
async def test_roles_with_finance_read_get_the_commercial_part_of_the_card(
    api: AsyncClient, auth_world: AuthWorld, who: str
) -> None:
    body = await card(api, who, auth_world.projects["east"])

    assert body["finance_visible"] is True
    assert body["planned_budget"] == "2400000000.00" and body["budget_currency"] == "KGS"
    assert body["customer_name"] == devseed.CUSTOMER and body["customer_id"]
    assert body["address"] and body["latitude"] and body["longitude"]


@pytest.mark.parametrize("who", BLIND)
async def test_internal_roles_without_finance_read_do_not_get_it_but_keep_the_rest(
    api: AsyncClient, auth_world: AuthWorld, who: str
) -> None:
    body = await card(api, who, auth_world.projects["east"])

    assert body["finance_visible"] is False
    assert {field: body[field] for field in COMMERCIAL} == dict.fromkeys(COMMERCIAL)
    assert body["name"] == devseed.EAST and body["lifecycle_stage"] == "construction"
    assert body["planned_start"] == "2025-03-01" and body["planned_end"] == "2028-12-31"
    assert body["node_count"] and body["status"] == "active"


@pytest.mark.parametrize("who", EXTERNAL)
async def test_external_roles_never_get_the_commercial_part(
    api: AsyncClient, auth_world: AuthWorld, who: str
) -> None:
    body = await card(api, who, auth_world.projects["east"])

    assert body["finance_visible"] is False
    assert {field: body[field] for field in COMMERCIAL} == dict.fromkeys(COMMERCIAL)


@pytest.mark.parametrize(("who", "visible"), [("foreman", True), ("master", False)])
async def test_the_list_of_objects_follows_the_same_rule(
    api: AsyncClient, auth_world: AuthWorld, who: str, visible: bool
) -> None:
    response = await api.get(P, headers=bearer(await token_for(api, who)))
    east = next(item for item in response.json() if item["id"] == str(auth_world.projects["east"]))

    assert response.status_code == 200 and east["finance_visible"] is visible
    assert (east["planned_budget"] is not None) is visible
    assert (east["address"] is not None) is visible


async def test_the_card_of_an_object_without_commercial_data_has_empty_fields_for_everyone(
    api: AsyncClient, auth_world: AuthWorld, finance_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    created = await api.post(
        P,
        headers={**bearer(owner), "Idempotency-Key": str(uuid4())},
        json={
            "name": f"FIN-{uuid4().hex[:8]}",
            "legal_entity_id": str(auth_world.legal_entities[devseed.LE_EAST]),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["finance_visible"] is True
    assert {field: body[field] for field in COMMERCIAL} == dict.fromkeys(COMMERCIAL)


async def test_commercial_fields_are_written_into_their_own_table_and_journaled(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, finance_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    entity = str(auth_world.legal_entities[devseed.LE_EAST])
    created = await api.post(
        P,
        headers={**bearer(owner), "Idempotency-Key": str(uuid4())},
        json={"name": f"FIN-{uuid4().hex[:8]}", "legal_entity_id": entity},
    )
    project = created.json()["id"]

    patched = await api.patch(
        f"{P}/{project}",
        headers=bearer(owner),
        json={
            "planned_budget": "1500000.50",
            "address": "ул. Бюджетная, 1",
            "lifecycle_stage": "design",
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["planned_budget"] == "1500000.50"
    assert patched.json()["lifecycle_stage"] == "design"  # поле самого объекта идёт рядом

    rows = await owner_rows(
        worker_engine,
        "SELECT planned_budget, address FROM project_finance WHERE project_id = :p",
        p=project,
    )
    assert [(row.planned_budget, row.address) for row in rows] == [
        (Decimal("1500000.50"), "ул. Бюджетная, 1")
    ]
    journal = await owner_rows(
        worker_engine,
        "SELECT action FROM audit_log WHERE table_name = 'project_finance' AND project_id = :p"
        " AND kind = 'data' ORDER BY id",
        p=project,
    )
    assert [row.action for row in journal] == ["insert"]

    again = await api.patch(
        f"{P}/{project}", headers=bearer(owner), json={"planned_budget": "2000000"}
    )
    assert again.json()["planned_budget"] == "2000000.00"
    journal = await owner_rows(
        worker_engine,
        "SELECT action FROM audit_log WHERE table_name = 'project_finance' AND project_id = :p"
        " AND kind = 'data' ORDER BY id",
        p=project,
    )
    assert [row.action for row in journal] == ["insert", "update"]


def test_the_api_refuses_to_write_commercial_fields_without_the_right_to_read_them() -> None:
    blind = Principal(
        user_id=uuid4(), full_name="x", role_code="technical_director", role_scope="group",
        role_ver=1, counterparty_id=None, permissions=frozenset({"objects:write"}),
        project_ids=frozenset(), legal_entity_ids=frozenset(), sid=uuid4(), amr=("pwd",),
        otp_at=None,
    )  # fmt: skip
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _require_finance_for({"planned_budget"}, blind)
    assert refused.value.status_code == 403
    _require_finance_for({"name", "planned_end"}, blind)  # без коммерческих полей - можно


# --- БД: политики project_finance ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "visible"), [(who, True) for who in READERS] + [(who, False) for who in BLIND]
)
async def test_the_database_returns_finance_rows_only_to_roles_with_finance_read(
    auth_world: AuthWorld, who: str, visible: bool
) -> None:
    rows = await call(
        ctx(auth_world, who),
        "SELECT planned_budget, address FROM project_finance WHERE project_id = :p",
        p=auth_world.projects["east"],
    )

    assert bool(rows) is visible
    if visible:
        assert rows[0].planned_budget == Decimal("2400000000.00")


@pytest.mark.parametrize("who", BLIND)
async def test_roles_without_finance_read_cannot_insert_or_update_finance_rows_even_in_sql(
    auth_world: AuthWorld, who: str
) -> None:
    east = auth_world.projects["east"]
    with pytest.raises(DBAPIError, match="row-level security"):
        await call(
            ctx(auth_world, who),
            "INSERT INTO project_finance (project_id, legal_entity_id, planned_budget)"
            " SELECT id, legal_entity_id, 1 FROM project WHERE id = :p",
            p=east,
        )
    changed = await call(
        ctx(auth_world, who),
        "UPDATE project_finance SET planned_budget = 1 WHERE project_id = :p RETURNING id",
        p=east,
    )
    assert changed == []


@pytest.mark.parametrize("who", ["customer", "sub_a"])
async def test_the_portal_pool_cannot_touch_the_finance_table(
    auth_world: AuthWorld, who: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await call(ctx(auth_world, who), "SELECT id FROM project_finance", portal=True)


async def test_the_project_table_no_longer_carries_the_commercial_columns(
    worker_engine: AsyncEngine,
) -> None:
    columns = {
        row.column_name
        for row in await owner_rows(
            worker_engine,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'project'",
        )
    }
    assert not columns & {
        "planned_budget", "budget_currency", "customer_id", "address", "latitude", "longitude",
        "warranty_until",
    }  # fmt: skip
    assert {"id", "name", "legal_entity_id", "lifecycle_stage", "planned_start"} <= columns


async def test_a_finance_row_keeps_the_project_and_the_legal_entity_it_was_created_with(
    auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    async with worker_engine.connect() as connection:
        for column in ("project_id", "legal_entity_id"):
            with pytest.raises(DBAPIError, match="is immutable"):
                await connection.execute(
                    text(f"UPDATE project_finance SET {column} = gen_random_uuid()")  # noqa: S608
                )
            await connection.rollback()


async def test_the_customer_of_a_finance_row_must_be_a_live_customer_counterparty(
    auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    async with worker_engine.connect() as connection:
        with pytest.raises(DBAPIError, match=r"project\.customer_invalid"):
            await connection.execute(
                text("UPDATE project_finance SET customer_id = :c WHERE project_id = :p"),
                {"c": auth_world.counterparties[devseed.SUB_A], "p": auth_world.projects["east"]},
            )
        await connection.rollback()


@pytest.mark.parametrize(
    ("who", "visible"), [(who, True) for who in READERS] + [(who, False) for who in BLIND]
)
async def test_the_journal_rows_of_the_finance_table_follow_finance_read(
    auth_world: AuthWorld, who: str, visible: bool
) -> None:
    """В журнале лежат старые и новые значения: без этой политики бюджет читался бы оттуда."""
    east = auth_world.projects["east"]
    sql = "SELECT count(*) AS n FROM audit_log WHERE table_name = :t AND project_id = :p"

    finance = await call(ctx(auth_world, who), sql, t="project_finance", p=east)
    other = await call(ctx(auth_world, who), sql, t="project", p=east)

    assert (finance[0].n > 0) is visible
    assert other[0].n > 0  # остальной журнал объекта остаётся читаемым


def test_two_first_saves_of_the_finance_card_are_a_conflict_not_a_server_error() -> None:
    diag = SimpleNamespace(
        message_primary="duplicate key value violates unique constraint",
        constraint_name="uq_project_finance_project_id",
    )
    translated = rule_error(DBAPIError("INSERT", {}, SimpleNamespace(diag=diag)))  # type: ignore[arg-type]

    assert translated is not None
    assert (translated.status_code, translated.detail) == (409, "finance_card_conflict")


# --- БД: функция права ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("master", (True, False, False)),
        ("foreman", (True, True, False)),
        ("techdir", (True, True, True)),
        ("estimator", (False, False, False)),
        ("owner", (False, False, False)),
    ],
)
async def test_has_permission_reads_the_role_of_the_user_in_the_request_context(
    auth_world: AuthWorld, who: str, expected: tuple[bool, bool, bool]
) -> None:
    rows = await call(
        ctx(auth_world, who),
        "SELECT app.has_permission('work_actual', 'write') AS w,"
        " app.has_permission('work_actual', 'approve') AS a,"
        " app.has_permission('work_actual', 'write', 'after_approval') AS e",
    )
    assert (rows[0].w, rows[0].a, rows[0].e) == expected


async def test_without_a_user_in_the_context_there_is_no_permission(auth_world: AuthWorld) -> None:
    rows = await call(None, "SELECT app.has_permission('finance', 'read') AS f")
    assert rows[0].f is False


# --- путь узла закрыт для логинов приложения ----------------------------------------------------


async def test_the_path_of_a_node_is_not_writable_by_the_application_logins(
    auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    east = auth_world.projects["east"]
    update_path = "UPDATE project_node SET path = path WHERE project_id = :p AND parent_id IS NULL"
    with pytest.raises(DBAPIError, match="permission denied"):
        await call(ctx(auth_world, "foreman"), update_path, p=east)
    async with worker_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(text(update_path), {"p": east})
        await connection.rollback()


async def test_the_other_columns_of_a_node_stay_writable_and_the_definer_owns_the_path(
    auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    privileges = {
        (row.login, row.column_name): row.allowed
        for row in await owner_rows(
            worker_engine,
            "SELECT l.login, c.column_name, has_column_privilege(l.login, 'public.project_node',"
            " c.column_name, 'UPDATE') AS allowed"
            " FROM (VALUES ('kut_app'), ('kut_worker'), ('kut_portal'), ('kut_auth')) l(login),"
            " (VALUES ('path'), ('name'), ('parent_id'), ('polygon')) c(column_name)",
        )
    }
    for login in ("kut_app", "kut_worker"):
        assert privileges[(login, "path")] is False
        assert all(privileges[(login, column)] for column in ("name", "parent_id", "polygon"))
    assert privileges[("kut_portal", "path")] is False
    assert privileges[("kut_auth", "path")] is True
