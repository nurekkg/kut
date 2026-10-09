"""S3a: API факта работ - цикл статусов по ролям, 404 на чужом объекте, предупреждение «без плана»,
геолокация, идемпотентность мобильных повторов, внешние роли."""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld, bearer, token_for
from tests.db_support import owner_rows
from tests.gpr_support import Plan, make_plan, purge

P = "/api/v1/projects"
DAY = "2026-09-03"


@pytest.fixture(autouse=True)
async def cleanup(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[None]:
    yield
    await purge(worker_engine, auth_world)


@pytest.fixture
async def plan(worker_engine: AsyncEngine, auth_world: AuthWorld) -> Plan:
    return await make_plan(worker_engine, auth_world)


def url(world: AuthWorld, *tail: str, project: str = "east") -> str:
    return "/".join([f"{P}/{world.projects[project]}/work-actuals", *tail])


async def send(
    api: AsyncClient,
    method: str,
    path: str,
    who: str,
    *,
    json: Any = None,
    key: UUID | None = None,
    params: Any = None,
) -> Response:
    headers = bearer(await token_for(api, who))
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return await api.request(method, path, headers=headers, json=json, params=params)


async def create(
    api: AsyncClient,
    world: AuthWorld,
    who: str,
    plan: Plan,
    *,
    key: UUID | None = None,
    **extra: Any,
) -> Response:
    body = {
        "node_id": str(plan.node),
        "work_id": str(plan.work),
        "volume": "10",
        "work_date": DAY,
        **extra,
    }
    return await send(api, "POST", url(world), who, json=body, key=key or uuid4())


async def fact_id(api: AsyncClient, world: AuthWorld, who: str, plan: Plan, **extra: Any) -> str:
    response = await create(api, world, who, plan, **extra)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def action(
    api: AsyncClient, world: AuthWorld, who: str, fact: str, name: str, **body: Any
) -> Response:
    return await send(api, "POST", url(world, fact, name), who, json=body or None)


# --- цикл статусов ----------------------------------------------------------------------------


async def test_a_master_enters_and_sends_and_a_foreman_confirms(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    created = await create(api, auth_world, "master", plan)
    assert created.status_code == 201, created.text
    fact = created.json()
    assert fact["status"] == "draft" and fact["author_name"] == "Мастер Восток (dev)"
    assert fact["schedule_item_id"] == str(plan.item) and fact["warnings"] == []

    sent = await action(api, auth_world, "master", fact["id"], "submit")
    assert (sent.status_code, sent.json()["status"]) == (200, "submitted")

    confirmed = await action(api, auth_world, "foreman", fact["id"], "approve")
    body = confirmed.json()
    assert (confirmed.status_code, body["status"]) == (200, "approved")
    assert body["approved_by_name"] == "Прораб Восток (dev)" and body["approved_at"]


async def test_a_master_may_send_a_fact_at_the_moment_of_entering_it(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    created = await create(api, auth_world, "master", plan, submit=True)

    assert (created.status_code, created.json()["status"]) == (201, "submitted")


@pytest.mark.parametrize("who", ["foreman", "techdir"])
async def test_the_fact_of_a_foreman_or_a_technical_director_is_approved_at_once(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan, who: str
) -> None:
    created = await create(api, auth_world, who, plan)

    body = created.json()
    assert (created.status_code, body["status"]) == (201, "approved")
    assert body["approved_by"] == body["author_id"] == str(auth_world.users[who])


async def test_a_master_cannot_approve_or_reject_not_even_his_own_fact(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    fact = await fact_id(api, auth_world, "master", plan, submit=True)

    approve = await action(api, auth_world, "master", fact, "approve")
    reject = await action(api, auth_world, "master", fact, "reject", reason="x")

    assert approve.status_code == 403 and reject.status_code == 403
    state = await send(api, "GET", url(auth_world, fact), "foreman")
    assert state.json()["status"] == "submitted"


async def test_a_rejected_fact_needs_a_reason_goes_back_to_the_author_and_can_be_resent(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    fact = await fact_id(api, auth_world, "master", plan, submit=True, volume="40")

    assert (await action(api, auth_world, "foreman", fact, "reject")).status_code == 422
    assert (
        await action(api, auth_world, "foreman", fact, "reject", reason="   ")
    ).status_code == 422
    rejected = await action(
        api, auth_world, "foreman", fact, "reject", reason="Нет фото бетонирования"
    )
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reject_reason"] == "Нет фото бетонирования"
    assert rejected.json()["approved_by"] is None

    fixed = await send(api, "PATCH", url(auth_world, fact), "master", json={"volume": "35"})
    assert (fixed.status_code, fixed.json()["volume"]) == (200, "35.000")
    resent = await action(api, auth_world, "master", fact, "submit")
    assert resent.json()["status"] == "submitted" and resent.json()["reject_reason"] is None
    assert (await action(api, auth_world, "foreman", fact, "approve")).json()[
        "status"
    ] == "approved"


async def test_the_database_refuses_impossible_transitions(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    draft = await fact_id(api, auth_world, "master", plan)
    approved = await fact_id(api, auth_world, "foreman", plan)

    cases = {
        "approve a draft": await action(api, auth_world, "foreman", draft, "approve"),
        "reject a draft": await action(api, auth_world, "foreman", draft, "reject", reason="x"),
        "send an approved fact back": await action(api, auth_world, "foreman", approved, "submit"),
        "reject an approved fact": await action(
            api, auth_world, "techdir", approved, "reject", reason="x"
        ),
    }
    for title, response in cases.items():
        assert response.status_code == 409, (title, response.text)
        assert response.json()["detail"] == "actual_transition_forbidden", title


async def test_repeating_a_transition_is_a_harmless_no_op_and_changes_nothing(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    """Мобильная связь повторяет запросы: второй approve или submit не ошибка и ничего не меняет."""
    fact = await fact_id(api, auth_world, "master", plan, submit=True)
    resent = await action(api, auth_world, "master", fact, "submit")
    first = await action(api, auth_world, "foreman", fact, "approve")
    again = await action(api, auth_world, "techdir", fact, "approve")

    assert resent.status_code == 200 and resent.json()["status"] == "submitted"
    assert first.status_code == again.status_code == 200
    assert (
        again.json()["approved_by"]
        == first.json()["approved_by"]
        == str(auth_world.users["foreman"])
    )
    assert (
        again.json()["approved_at"] == first.json()["approved_at"]
    )  # подтвердивший не перезаписан


async def test_a_submitted_fact_is_locked_and_only_its_author_edits_a_draft(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    fact = await fact_id(api, auth_world, "master", plan)

    other = await send(api, "PATCH", url(auth_world, fact), "foreman", json={"volume": "12"})
    own = await send(api, "PATCH", url(auth_world, fact), "master", json={"volume": "12"})
    foreign_send = await action(api, auth_world, "foreman", fact, "submit")  # чужой черновик
    await action(api, auth_world, "master", fact, "submit")
    locked = await send(api, "PATCH", url(auth_world, fact), "master", json={"volume": "13"})
    locked_for_foreman = await send(
        api, "PATCH", url(auth_world, fact), "foreman", json={"volume": "13"}
    )

    assert other.status_code == 403 and own.status_code == 200
    assert foreign_send.status_code == 403  # отправляет только автор
    assert (locked.status_code, locked.json()["detail"]) == (409, "actual_locked")
    assert (locked_for_foreman.status_code, locked_for_foreman.json()["detail"]) == (
        409,
        "actual_locked",
    )


async def test_after_approval_only_the_technical_director_edits_and_the_edit_is_journaled(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    fact = await fact_id(api, auth_world, "foreman", plan, volume="20")

    for who in ("master", "foreman"):
        refused = await send(api, "PATCH", url(auth_world, fact), who, json={"volume": "21"})
        assert refused.status_code == 403, who
    edited = await send(api, "PATCH", url(auth_world, fact), "techdir", json={"volume": "22.5"})

    assert (edited.status_code, edited.json()["volume"]) == (200, "22.500")
    assert edited.json()["status"] == "approved"
    journal = await owner_rows(
        worker_engine,
        "SELECT actor_id, action, old->>'volume' AS old_volume, new->>'volume' AS new_volume"
        " FROM audit_log WHERE table_name = 'work_actual' AND row_id = :id AND kind = 'data'"
        " ORDER BY id",
        id=UUID(fact),
    )
    assert [row.action for row in journal] == ["insert", "update"]
    assert journal[-1].actor_id == auth_world.users["techdir"]
    assert (float(journal[-1].old_volume), float(journal[-1].new_volume)) == (20, 22.5)


@pytest.mark.parametrize("who", ["estimator", "owner", "finance", "customer", "sub_a"])
async def test_roles_without_the_write_permission_cannot_enter_a_fact(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan, who: str
) -> None:
    response = await create(api, auth_world, who, plan)

    assert response.status_code == 403, response.text


# --- чужой объект ---------------------------------------------------------------------------------


async def test_a_fact_on_a_foreign_object_is_not_found(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    fact = await fact_id(api, auth_world, "master", plan)
    west = url(auth_world, project="west")
    body = {"node_id": str(plan.node), "work_id": str(plan.work), "volume": "1", "work_date": DAY}

    responses = [
        await send(api, "POST", west, "master", json=body, key=uuid4()),
        await send(api, "GET", west, "master"),
        await send(api, "GET", f"{west}/{fact}", "master"),
        await send(api, "POST", f"{west}/{fact}/submit", "master"),
        await send(api, "POST", f"{west}/{fact}/approve", "foreman"),
    ]

    assert [response.status_code for response in responses] == [404] * 5


async def test_a_fact_id_from_another_object_does_not_open_through_a_visible_one(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    fact = await fact_id(api, auth_world, "techdir", plan)
    # techdir видит обе стройки, но объект в пути другой: факт «Востока» через «Запад» не найти
    through_west = await send(api, "GET", url(auth_world, fact, project="west"), "techdir")

    assert through_west.status_code == 404


async def test_a_node_from_another_object_cannot_hold_a_fact(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    async with worker_engine.begin() as connection:
        west_node = await connection.scalar(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
                " SELECT id, legal_entity_id, 'building', 'S3A-' || gen_random_uuid()::text"
                " FROM project WHERE id = :p RETURNING id"
            ),
            {"p": auth_world.projects["west"]},
        )

    response = await send(
        api, "POST", url(auth_world), "foreman",
        json={
            "node_id": str(west_node), "work_id": str(plan.work), "volume": "1", "work_date": DAY,
        },
        key=uuid4(),
    )  # fmt: skip

    assert (response.status_code, response.json()["detail"]) == (422, "actual_node_invalid")


# --- план -----------------------------------------------------------------------------------------


async def test_a_fact_without_a_plan_is_allowed_with_a_warning(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    bare = await make_plan(worker_engine, auth_world, with_item=False)

    created = await create(api, auth_world, "master", bare)

    body = created.json()
    assert created.status_code == 201, created.text
    assert body["schedule_item_id"] is None and body["unplanned"] is True
    assert body["warnings"] == ["unplanned"]


async def test_a_fact_points_only_at_the_schedule_row_of_its_own_node_and_work(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    other = await make_plan(worker_engine, auth_world, with_item=False)

    response = await create(api, auth_world, "master", other, schedule_item_id=str(plan.item))

    assert (response.status_code, response.json()["detail"]) == (422, "actual_item_invalid")


# --- значения -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"volume": "0"},
        {"volume": "-1"},
        {"volume": "1.2345"},
        {"latitude": "91", "longitude": "74"},
        {"latitude": "-91", "longitude": "74"},
        {"latitude": "42", "longitude": "181"},
        {"latitude": "42", "longitude": "-181"},
        {"latitude": "42"},
        {"longitude": "74"},
        {"latitude": "42", "longitude": "74", "accuracy_m": "-1"},
        {"accuracy_m": "5"},
        {"unknown_field": 1},
    ],
)
async def test_wrong_values_and_geolocation_are_refused_with_422(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan, overrides: dict[str, Any]
) -> None:
    response = await create(api, auth_world, "master", plan, **overrides)

    assert response.status_code == 422, response.text


async def test_geolocation_from_a_browser_is_rounded_and_stored(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    response = await create(
        api, auth_world, "master", plan,
        latitude=42.87460012345, longitude=74.56980098765, accuracy_m=8.4999,
    )  # fmt: skip

    body = response.json()
    assert response.status_code == 201, response.text
    assert (body["latitude"], body["longitude"], body["accuracy_m"]) == (
        "42.874600",
        "74.569801",
        "8.50",
    )


async def test_the_date_cannot_be_in_the_future(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    future = (date.today() + timedelta(days=2)).isoformat()

    created = await create(api, auth_world, "master", plan, work_date=future)
    fact = await fact_id(api, auth_world, "master", plan)
    edited = await send(api, "PATCH", url(auth_world, fact), "master", json={"work_date": future})

    assert (created.status_code, created.json()["detail"]) == (422, "actual_date_in_future")
    assert (edited.status_code, edited.json()["detail"]) == (422, "actual_date_in_future")
    today = await create(api, auth_world, "master", plan, work_date=date.today().isoformat())
    assert today.status_code == 201


# --- идемпотентность ----------------------------------------------------------------------------


async def test_a_repeated_request_after_a_lost_response_does_not_duplicate_the_fact(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    key = uuid4()
    first = await create(api, auth_world, "master", plan, key=key, volume="7")
    again = await create(api, auth_world, "master", plan, key=key, volume="7")
    other = await create(api, auth_world, "master", plan, key=key, volume="8")
    keyless = await send(
        api, "POST", url(auth_world), "master",
        json={
            "node_id": str(plan.node), "work_id": str(plan.work), "volume": "7", "work_date": DAY,
        },
    )  # fmt: skip

    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json() and again.headers["Idempotent-Replayed"] == "true"
    assert other.status_code == 422 and other.json()["detail"] == "idempotency_key_reused"
    assert keyless.status_code == 422
    rows = await owner_rows(
        worker_engine,
        "SELECT count(*) AS n FROM work_actual WHERE node_id = :n AND deleted_at IS NULL",
        n=plan.node,
    )
    assert rows[0].n == 1


# --- чтение ---------------------------------------------------------------------------------------


async def test_the_list_filters_by_status_author_date_and_node(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    draft = await fact_id(api, auth_world, "master", plan, work_date="2026-09-02")
    sent = await fact_id(api, auth_world, "master", plan, work_date="2026-09-04", submit=True)
    approved = await fact_id(api, auth_world, "foreman", plan, work_date="2026-09-05")
    node = {"node_id": str(plan.node)}

    async def ids(who: str, **params: Any) -> list[str]:
        response = await send(api, "GET", url(auth_world), who, params={**node, **params})
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()]

    assert set(await ids("foreman")) == {draft, sent, approved}
    assert (await ids("foreman"))[0] == approved  # свежие даты сверху
    assert set(await ids("foreman", status=["draft", "submitted"])) == {draft, sent}
    assert await ids("foreman", status="approved") == [approved]
    assert set(await ids("master", mine="true")) == {draft, sent}
    assert await ids("foreman", author_id=str(auth_world.users["foreman"])) == [approved]
    assert set(await ids("foreman", date_from="2026-09-04")) == {sent, approved}
    assert await ids("foreman", date_to="2026-09-02") == [draft]
    assert (
        await send(api, "GET", url(auth_world), "foreman", params={"status": "nonsense"})
    ).status_code == 422


async def test_external_roles_see_only_approved_facts_and_no_people_or_geolocation(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    draft = await fact_id(api, auth_world, "master", plan, volume="1")
    approved = await fact_id(
        api, auth_world, "foreman", plan, volume="2", latitude="42.8746", longitude="74.5698"
    )

    for who in ("customer", "sub_a"):
        listed = await send(api, "GET", url(auth_world), who, params={"node_id": str(plan.node)})
        rows = {row["id"]: row for row in listed.json()}
        assert set(rows) == {approved}, who
        row = rows[approved]
        assert row["status"] == "approved" and row["volume"] == "2.000", who
        for hidden in (
            "author_id",
            "author_name",
            "approved_by",
            "reject_reason",
            "latitude",
            "longitude",
            "photo_count",
        ):
            assert row[hidden] is None, (who, hidden)
        assert (await send(api, "GET", url(auth_world, draft), who)).status_code == 404, who
        assert (await send(api, "GET", url(auth_world, approved), who)).status_code == 200, who


@pytest.mark.parametrize("params", [{"offset": 99999999999999999999}, {"limit": 5001}])
async def test_paging_outside_the_bounds_is_refused_not_a_server_error(
    api: AsyncClient, auth_world: AuthWorld, params: dict[str, Any]
) -> None:
    response = await send(api, "GET", url(auth_world), "foreman", params=params)

    assert response.status_code == 422, response.text


# --- удаление -------------------------------------------------------------------------------------


async def test_an_author_deletes_his_draft_and_only_the_technical_director_an_approved_fact(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    draft = await fact_id(api, auth_world, "master", plan)
    approved = await fact_id(api, auth_world, "foreman", plan)

    assert (await send(api, "DELETE", url(auth_world, draft), "foreman")).status_code == 403
    assert (await send(api, "DELETE", url(auth_world, draft), "master")).status_code == 204
    assert (await send(api, "GET", url(auth_world, draft), "master")).status_code == 404
    assert (await send(api, "DELETE", url(auth_world, draft), "master")).status_code == 404

    assert (await send(api, "DELETE", url(auth_world, approved), "foreman")).status_code == 403
    assert (await send(api, "DELETE", url(auth_world, approved), "techdir")).status_code == 204


async def test_a_node_with_a_fact_cannot_be_deleted_even_without_a_schedule_row(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    bare = await make_plan(worker_engine, auth_world, with_item=False)
    fact = await fact_id(api, auth_world, "foreman", bare)
    node_path = f"{P}/{bare.project}/nodes/{bare.node}"

    refused = await send(api, "DELETE", node_path, "foreman")
    await send(api, "DELETE", url(auth_world, fact), "techdir")
    allowed = await send(api, "DELETE", node_path, "foreman")

    assert (refused.status_code, refused.json()["detail"]) == (409, "node_has_dependents")
    assert allowed.status_code == 204
