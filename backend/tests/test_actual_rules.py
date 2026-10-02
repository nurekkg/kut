"""S3a: статусы факта работ и права на переходы живут в БД (триггер app.work_actual_guard).

Ходим под kut_app с контекстом настоящих пользователей dev-сида: право по роли БД берёт из
app.has_permission, поэтому мастер, прораб и техдиректор ведут себя так же, как через API.
"""

from collections.abc import AsyncIterator
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld
from tests.db_support import call, owner_rows
from tests.gpr_support import Plan, ctx, make_plan, purge

DAY = date(2026, 9, 3)

INSERT = (
    "INSERT INTO work_actual (project_id, legal_entity_id, node_id, work_id, schedule_item_id,"
    " volume, work_date, author_id, status, latitude, longitude, accuracy_m)"
    " VALUES (:p, :le, :node, :work, :item, :volume, :day, :author, :status, :lat, :lon, :acc)"
    " RETURNING id, status, author_id, approved_by, approved_at, schedule_item_id"
)


@pytest.fixture(scope="module")
async def plan(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[Plan]:
    made = await make_plan(worker_engine, auth_world)
    yield made
    await purge(worker_engine, auth_world)


async def fact(
    world: AuthWorld, who: str, plan: Plan, *, status: str = "draft", **overrides: Any
) -> Any:
    params: dict[str, Any] = {
        "p": plan.project,
        "le": plan.entity,
        "node": plan.node,
        "work": plan.work,
        "item": None,
        "volume": "10",
        "day": DAY,
        "author": world.users["owner"],  # чужой автор: БД всё равно запишет того, кто вносит
        "status": status,
        "lat": None,
        "lon": None,
        "acc": None,
        **overrides,
    }
    return (await call(ctx(world, who), INSERT, **params))[0]


async def state(engine: AsyncEngine, fact_id: UUID) -> Any:
    rows = await owner_rows(
        engine,
        "SELECT status, volume, author_id, approved_by, approved_at, reject_reason, deleted_at,"
        " schedule_item_id FROM work_actual WHERE id = :id",
        id=fact_id,
    )
    return rows[0]


async def update(world: AuthWorld, who: str, fact_id: UUID, **sets: Any) -> None:
    assignments = ", ".join(f"{column} = :{column}" for column in sets)
    await call(
        ctx(world, who),
        f"UPDATE work_actual SET {assignments} WHERE id = :id",  # noqa: S608
        id=fact_id,
        **sets,
    )


async def refuses(world: AuthWorld, who: str, fact_id: UUID, message: str, **sets: Any) -> None:
    with pytest.raises(IntegrityError, match=message):
        await update(world, who, fact_id, **sets)


async def soft_delete(world: AuthWorld, who: str, fact_id: UUID) -> None:
    await call(ctx(world, who), "UPDATE work_actual SET deleted_at = now() WHERE id = :id", id=fact_id)


# --- создание -------------------------------------------------------------------------------


async def test_a_master_creates_a_draft_and_the_author_is_the_master_not_the_value_sent(
    auth_world: AuthWorld, plan: Plan
) -> None:
    row = await fact(auth_world, "master", plan)
    assert row.status == "draft"
    assert row.author_id == auth_world.users["master"]
    assert row.approved_by is None and row.approved_at is None


async def test_a_master_may_send_the_fact_at_once(auth_world: AuthWorld, plan: Plan) -> None:
    assert (await fact(auth_world, "master", plan, status="submitted")).status == "submitted"


@pytest.mark.parametrize("status", ["approved", "rejected"])
async def test_a_master_cannot_create_an_approved_or_rejected_fact(
    auth_world: AuthWorld, plan: Plan, status: str
) -> None:
    with pytest.raises(IntegrityError, match=r"actual\.transition_forbidden"):
        await fact(auth_world, "master", plan, status=status)


@pytest.mark.parametrize("who", ["foreman", "techdir"])
async def test_the_fact_of_a_foreman_or_a_technical_director_is_approved_at_once(
    auth_world: AuthWorld, plan: Plan, who: str
) -> None:
    row = await fact(auth_world, who, plan, status="draft")
    assert row.status == "approved"
    assert row.approved_by == auth_world.users[who] == row.author_id
    assert row.approved_at is not None


@pytest.mark.parametrize("who", ["owner", "estimator", "finance"])
async def test_roles_without_the_write_permission_cannot_create_a_fact(
    auth_world: AuthWorld, plan: Plan, who: str
) -> None:
    with pytest.raises(IntegrityError, match=r"actual\.forbidden"):
        await fact(auth_world, who, plan)


async def test_a_fact_finds_its_schedule_row_by_node_and_work_and_may_exist_without_one(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    assert (await fact(auth_world, "master", plan)).schedule_item_id == plan.item
    other = await make_plan(worker_engine, auth_world, with_item=False)
    assert (await fact(auth_world, "master", other)).schedule_item_id is None


async def test_a_fact_cannot_point_at_the_schedule_row_of_another_node(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    other = await make_plan(worker_engine, auth_world, with_item=False)
    with pytest.raises(IntegrityError, match="foreign key"):
        await fact(auth_world, "master", other, item=plan.item)


# --- переходы статусов ----------------------------------------------------------------------


async def test_the_whole_cycle_draft_submitted_rejected_corrected_submitted_approved(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    created = (await fact(auth_world, "master", plan, volume="40")).id
    await update(auth_world, "master", created, status="submitted")
    assert (await state(worker_engine, created)).status == "submitted"

    await refuses(auth_world, "foreman", created, r"actual\.reason_required", status="rejected")
    await refuses(
        auth_world, "foreman", created, r"actual\.reason_required",
        status="rejected", reject_reason="  ",
    )  # fmt: skip
    await update(
        auth_world, "foreman", created, status="rejected", reject_reason="Нет фото бетонирования"
    )
    rejected = await state(worker_engine, created)
    assert rejected.status == "rejected" and rejected.reject_reason == "Нет фото бетонирования"
    assert rejected.approved_by is None

    await update(auth_world, "master", created, volume="35")  # автор правит отклонённый факт
    await update(auth_world, "master", created, status="submitted")
    resent = await state(worker_engine, created)
    assert resent.status == "submitted" and resent.reject_reason is None
    assert resent.volume == 35

    await update(auth_world, "foreman", created, status="approved")
    approved = await state(worker_engine, created)
    assert approved.status == "approved" and approved.reject_reason is None
    assert approved.approved_by == auth_world.users["foreman"] and approved.approved_at is not None


async def test_a_master_cannot_approve_or_reject_a_fact_not_even_his_own(
    auth_world: AuthWorld, plan: Plan
) -> None:
    created = (await fact(auth_world, "master", plan, status="submitted")).id
    await refuses(auth_world, "master", created, r"actual\.forbidden", status="approved")
    await refuses(
        auth_world, "master", created, r"actual\.forbidden", status="rejected", reject_reason="x"
    )


@pytest.mark.parametrize("target", ["approved", "rejected"])
async def test_a_draft_cannot_be_approved_or_rejected_by_the_foreman(
    auth_world: AuthWorld, plan: Plan, target: str
) -> None:
    created = (await fact(auth_world, "master", plan)).id
    extra = {"reject_reason": "x"} if target == "rejected" else {}
    await refuses(
        auth_world, "foreman", created, r"actual\.transition_forbidden", status=target, **extra
    )


async def test_a_submitted_fact_is_locked_for_its_author_and_for_the_foreman(
    auth_world: AuthWorld, plan: Plan
) -> None:
    created = (await fact(auth_world, "master", plan, status="submitted")).id
    await refuses(auth_world, "master", created, r"actual\.locked", volume="11")
    await refuses(auth_world, "foreman", created, r"actual\.locked", volume="11")


async def test_only_the_author_edits_a_draft(auth_world: AuthWorld, plan: Plan) -> None:
    created = (await fact(auth_world, "master", plan)).id
    await refuses(auth_world, "foreman", created, r"actual\.forbidden", volume="12")
    await update(auth_world, "master", created, volume="12")


async def test_a_user_cannot_forge_the_approver_or_the_reason(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    created = (await fact(auth_world, "master", plan)).id
    await update(
        auth_world, "master", created, approved_by=auth_world.users["foreman"], reject_reason="x"
    )
    row = await state(worker_engine, created)
    assert row.status == "draft" and row.approved_by is None and row.reject_reason is None


# --- после согласования -----------------------------------------------------------------------


async def test_after_approval_only_the_technical_director_edits_and_every_edit_is_journaled(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    created = (await fact(auth_world, "foreman", plan, volume="20")).id

    for who in ("master", "foreman"):
        await refuses(auth_world, who, created, r"actual\.forbidden", volume="21")

    await update(auth_world, "techdir", created, volume="22.5")
    row = await state(worker_engine, created)
    assert row.status == "approved" and row.volume == 22.5

    journal = await owner_rows(
        worker_engine,
        "SELECT actor_id, action, old->>'volume' AS old_volume, new->>'volume' AS new_volume"
        " FROM audit_log WHERE table_name = 'work_actual' AND row_id = :id AND kind = 'data'"
        " ORDER BY id",
        id=created,
    )
    last = journal[-1]
    assert last.action == "update" and last.actor_id == auth_world.users["techdir"]
    assert float(last.old_volume) == 20 and float(last.new_volume) == 22.5


async def test_an_approved_fact_cannot_go_back_to_another_status_even_for_the_technical_director(
    auth_world: AuthWorld, plan: Plan
) -> None:
    created = (await fact(auth_world, "foreman", plan)).id
    await refuses(auth_world, "techdir", created, r"actual\.transition_forbidden", status="draft")


# --- удаление -----------------------------------------------------------------------------------


async def test_the_author_deletes_his_draft_but_an_approved_fact_only_the_technical_director(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    draft = (await fact(auth_world, "master", plan)).id
    await soft_delete(auth_world, "master", draft)
    assert (await state(worker_engine, draft)).deleted_at is not None

    approved = (await fact(auth_world, "foreman", plan)).id
    with pytest.raises(IntegrityError, match=r"actual\.forbidden"):
        await soft_delete(auth_world, "foreman", approved)
    await soft_delete(auth_world, "techdir", approved)
    assert (await state(worker_engine, approved)).deleted_at is not None


async def test_a_fact_with_live_photos_cannot_be_deleted(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    created = (await fact(auth_world, "foreman", plan)).id
    async with worker_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO work_photo (project_id, legal_entity_id, work_actual_id, s3_key,"
                " content_type, size_bytes, uploaded_by) VALUES (:p, :le, :fact,"
                " 'project/' || CAST(CAST(:p AS uuid) AS text) || '/fact/x/' || gen_random_uuid() || '.jpg',"
                " 'image/jpeg', 1000, :u)"
            ),
            {"p": plan.project, "le": plan.entity, "fact": created, "u": auth_world.users["master"]},
        )
    with pytest.raises(IntegrityError, match=r"actual\.has_dependents"):
        await soft_delete(auth_world, "techdir", created)


# --- проверки значений ------------------------------------------------------------------------


async def test_the_date_of_a_fact_cannot_be_in_the_future(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    today = (await owner_rows(worker_engine, "SELECT app.local_today() AS day"))[0].day
    tomorrow = (await owner_rows(worker_engine, "SELECT app.local_today() + 1 AS day"))[0].day
    with pytest.raises(IntegrityError, match=r"actual\.date_in_future"):
        await fact(auth_world, "master", plan, day=tomorrow)
    created = (await fact(auth_world, "master", plan, day=today)).id
    await refuses(auth_world, "master", created, r"actual\.date_in_future", work_date=tomorrow)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"volume": "0"}, "ck_work_actual_volume_positive"),
        ({"volume": "-1"}, "ck_work_actual_volume_positive"),
        ({"lat": "91", "lon": "74"}, "ck_work_actual_coordinates_valid"),
        ({"lat": "42", "lon": "181"}, "ck_work_actual_coordinates_valid"),
        ({"lat": "42"}, "ck_work_actual_coordinates_valid"),
        ({"lat": "42", "lon": "74", "acc": "-1"}, "ck_work_actual_accuracy_valid"),
        ({"acc": "5"}, "ck_work_actual_accuracy_valid"),
    ],
)
async def test_the_database_checks_the_volume_and_the_geolocation(
    auth_world: AuthWorld, plan: Plan, overrides: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        await fact(auth_world, "master", plan, **overrides)


async def test_a_fact_with_valid_geolocation_is_stored(auth_world: AuthWorld, plan: Plan) -> None:
    row = await fact(auth_world, "master", plan, lat="42.874600", lon="74.569800", acc="8.5")
    assert row.status == "draft"


async def test_a_fact_cannot_use_a_node_from_another_project_or_a_deleted_node(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    west = auth_world.projects["west"]
    async with worker_engine.begin() as connection:
        entity = await connection.scalar(
            text("SELECT legal_entity_id FROM project WHERE id = :id"), {"id": west}
        )
        foreign = await connection.scalar(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
                " VALUES (:p, :le, 'building', 'S3A-' || gen_random_uuid()::text) RETURNING id"
            ),
            {"p": west, "le": entity},
        )
    wrong = Plan(
        project=plan.project, entity=plan.entity, node=foreign, work=plan.work, item=plan.item
    )
    with pytest.raises(IntegrityError, match=r"actual\.node_invalid"):
        await fact(auth_world, "master", wrong)

    gone = await make_plan(worker_engine, auth_world, with_item=False)
    async with worker_engine.begin() as connection:
        await connection.execute(
            text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": gone.node}
        )
    with pytest.raises(IntegrityError, match=r"actual\.node_invalid"):
        await fact(auth_world, "master", gone)


async def test_a_foreman_of_one_object_cannot_write_a_fact_into_another(
    auth_world: AuthWorld, worker_engine: AsyncEngine, plan: Plan
) -> None:
    before = await owner_rows(
        worker_engine, "SELECT count(*) AS n FROM work_actual WHERE project_id = :p",
        p=auth_world.projects["west"],
    )  # fmt: skip
    foreign = Plan(
        project=auth_world.projects["west"], entity=plan.entity, node=plan.node, work=plan.work, item=None
    )
    with pytest.raises(DBAPIError):
        await fact(auth_world, "foreman", foreign)
    after = await owner_rows(
        worker_engine, "SELECT count(*) AS n FROM work_actual WHERE project_id = :p",
        p=auth_world.projects["west"],
    )  # fmt: skip
    assert before[0].n == after[0].n


# --- внешние роли -----------------------------------------------------------------------------


async def test_external_roles_read_only_approved_facts_and_no_people(
    auth_world: AuthWorld, plan: Plan
) -> None:
    draft = (await fact(auth_world, "master", plan, volume="1")).id
    approved = (await fact(auth_world, "foreman", plan, volume="2")).id
    for who in ("customer", "sub_a"):
        rows = await call(
            ctx(auth_world, who),
            "SELECT id, status, volume FROM work_actual WHERE node_id = :n",
            portal=True,
            n=plan.node,
        )
        ids = {row.id for row in rows}
        assert approved in ids and draft not in ids
        assert {row.status for row in rows} == {"approved"}
        for column in ("author_id", "approved_by", "reject_reason", "latitude"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await call(
                    ctx(auth_world, who),
                    f"SELECT {column} FROM work_actual LIMIT 1",  # noqa: S608
                    portal=True,
                )


async def test_the_portal_pool_cannot_write_facts_or_read_photos(
    auth_world: AuthWorld, plan: Plan
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await call(
            ctx(auth_world, "customer"),
            INSERT,
            portal=True,
            p=plan.project, le=plan.entity, node=plan.node, work=plan.work, item=None, volume="1",
            day=DAY, author=auth_world.users["customer"], status="draft",
            lat=None, lon=None, acc=None,
        )  # fmt: skip
    with pytest.raises(DBAPIError, match="permission denied"):
        await call(ctx(auth_world, "customer"), "SELECT id FROM work_photo", portal=True)
