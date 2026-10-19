"""Маршруты согласования версий (S6a-2, ADR-015 часть 5): шаги, автор, отказ с причиной,
повтор, снимок,
замещение, видимость причин, подписанный скан, очередь, права шаблона; прямой SQL мимо сервиса
на гарды."""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.core.context import use_context
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.documents_support import (
    API,
    PDF,
    PDF_OK,
    Desk,
    create_document,
    leave_no_access_behind,  # noqa: F401
    open_desk,
    sha256_b64,
    upload,
)
from tests.equipment_support import idem, in_context, rows

MONEY_DESK = ("owner", "foreman", "estimator", "techdir", "finance", "master", "accountant")


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


@pytest.fixture(autouse=True)
async def forget_the_granted_rights() -> AsyncIterator[None]:
    yield
    await get_redis().flushdb()


async def local_today(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text("SELECT app.local_today()")).scalar_one())


async def money_version(
    api: AsyncClient, s3: httpx.AsyncClient, desk: Desk, author: str, auth_world: AuthWorld
) -> tuple[str, str]:
    created = await create_document(
        api,
        desk.heads[author],
        {
            "kind": "ks2",
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "counterparty_id": str(auth_world.counterparties[devseed.CUSTOMER]),
            "title": "КС-2 на согласовании",
        },
    )
    assert created.status_code == 201, created.text
    document = created.json()["id"]
    uploaded = await upload(api, s3, document, desk.heads[author], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    return document, uploaded.json()["id"]


async def other_version(
    api: AsyncClient, s3: httpx.AsyncClient, desk: Desk, author: str
) -> tuple[str, str]:
    created = await create_document(
        api,
        desk.heads[author],
        {
            "kind": "commissioning_act",
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "title": "Акт на согласовании",
        },
    )
    assert created.status_code == 201, created.text
    document = created.json()["id"]
    uploaded = await upload(api, s3, document, desk.heads[author], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    return document, uploaded.json()["id"]


async def send(
    api: AsyncClient, headers: dict[str, str], document: str, version: str
) -> httpx.Response:
    return await api.post(
        f"{API}/documents/{document}/versions/{version}/send", headers={**headers, **idem()}
    )


async def decide(
    api: AsyncClient,
    headers: dict[str, str],
    document: str,
    version: str,
    position: int,
    decision: str,
    reason: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"position": position, "decision": decision}
    if reason is not None:
        body["reason"] = reason
    return await api.post(
        f"{API}/documents/{document}/versions/{version}/decisions",
        json=body,
        headers={**headers, **idem()},
    )


async def route_of(
    api: AsyncClient, headers: dict[str, str], document: str, version: str
) -> dict[str, Any]:
    response = await api.get(
        f"{API}/documents/{document}/versions/{version}/route", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_money_route_runs_four_steps_to_approval(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    sent = await send(api, desk.heads["accountant"], document, version)
    assert sent.status_code == 200, sent.text
    assert [step["role_code"] for step in sent.json()["steps"]] == [
        "foreman",
        "estimator",
        "technical_director",
        "finance_director",
    ]
    for position, who in ((1, "foreman"), (2, "estimator"), (3, "techdir")):
        decided = await decide(api, desk.heads[who], document, version, position, "approve")
        assert decided.status_code == 200, decided.text
    last = await decide(api, desk.heads["finance"], document, version, 4, "approve")
    assert last.status_code == 200, last.text
    final = last.json()
    assert final["version_status"] == "approved"
    assert final["current_position"] is None
    assert [step["status"] for step in final["steps"]] == ["approved"] * 4
    assert len(final["decisions"]) == 4


async def test_only_the_current_step_decides_and_the_author_cannot(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "estimator", auth_world)
    assert (await send(api, desk.heads["estimator"], document, version)).status_code == 200
    skipped = await decide(api, desk.heads["techdir"], document, version, 3, "approve")
    assert skipped.status_code == 403, skipped.text
    assert skipped.json()["detail"] == "approval.not_current_step"
    await decide(api, desk.heads["foreman"], document, version, 1, "approve")
    author = await decide(api, desk.heads["estimator"], document, version, 2, "approve")
    assert author.status_code == 403, author.text
    assert author.json()["detail"] == "approval.author_forbidden"
    unchanged = await route_of(api, desk.heads["owner"], document, version)
    assert unchanged["current_position"] == 2
    assert len(unchanged["decisions"]) == 1


async def test_reject_needs_a_reason_and_sends_the_version_back_to_draft(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200
    assert (
        await decide(api, desk.heads["foreman"], document, version, 1, "approve")
    ).status_code == 200
    blank = await decide(api, desk.heads["estimator"], document, version, 2, "reject", reason="  ")
    assert blank.status_code == 422, blank.text
    assert blank.json()["detail"] == "approval.reason_required"
    rejected = await decide(
        api, desk.heads["estimator"], document, version, 2, "reject", reason="Не сходятся объёмы"
    )
    assert rejected.status_code == 200, rejected.text
    state = rejected.json()
    assert state["version_status"] == "draft"
    assert [step["status"] for step in state["steps"]] == [
        "approved",
        "rejected",
        "skipped",
        "skipped",
    ]
    assert state["decisions"][-1]["reason"] == "Не сходятся объёмы"
    resent = await send(api, desk.heads["accountant"], document, version)
    assert resent.status_code == 200, resent.text
    assert resent.json()["attempts"] == 2
    assert resent.json()["current_position"] == 1


async def test_a_repeated_decision_is_a_no_op(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200
    first = await decide(api, desk.heads["foreman"], document, version, 1, "approve")
    again = await decide(api, desk.heads["foreman"], document, version, 1, "approve")
    assert first.status_code == 200 and again.status_code == 200, again.text
    assert len(again.json()["decisions"]) == 1
    other = await decide(api, desk.heads["foreman"], document, version, 1, "reject", reason="нет")
    assert other.status_code == 403, other.text


async def test_a_template_change_does_not_touch_a_running_route(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200
    saved = await api.put(
        f"{API}/documents/route-templates/money",
        json={"roles": ["foreman", "technical_director"]},
        headers=desk.heads["owner"],
    )
    assert saved.status_code == 200, saved.text
    running = await route_of(api, desk.heads["owner"], document, version)
    assert [step["role_code"] for step in running["steps"]] == [
        "foreman",
        "estimator",
        "technical_director",
        "finance_director",
    ]
    restored = await api.put(
        f"{API}/documents/route-templates/money",
        json={"roles": ["foreman", "estimator", "technical_director", "finance_director"]},
        headers=desk.heads["owner"],
    )
    assert restored.status_code == 200, restored.text


async def test_the_route_template_is_changed_only_by_owner_and_technical_director(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    refused = await api.put(
        f"{API}/documents/route-templates/other",
        json={"roles": ["foreman"]},
        headers=desk.heads["estimator"],
    )
    assert refused.status_code == 403, refused.text
    duplicate = await api.put(
        f"{API}/documents/route-templates/other",
        json={"roles": ["foreman", "foreman"]},
        headers=desk.heads["owner"],
    )
    assert duplicate.status_code == 422, duplicate.text
    assert duplicate.json()["detail"] == "approval.template_invalid"
    listed = await api.get(f"{API}/documents/route-templates", headers=desk.heads["owner"])
    assert listed.status_code == 200, listed.text
    codes = {item["code"]: [s["role_code"] for s in item["steps"]] for item in listed.json()}
    assert codes["other"] == ["foreman", "technical_director"]


async def test_a_substitute_decides_for_the_delegator_and_the_record_names_both(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    today = await local_today(worker_sync_engine)
    tomorrow = str(date.fromisoformat(today) + timedelta(days=1))
    past = str(date.fromisoformat(today) - timedelta(days=1))
    in_the_past = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["owner"]),
            "role_code": "finance_director",
            "valid_from": past,
            "valid_to": tomorrow,
        },
        headers={**desk.heads["finance"], **idem()},
    )
    assert in_the_past.status_code == 422, in_the_past.text
    assert in_the_past.json()["detail"] == "approval.period_in_past"
    wrong_role = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["owner"]),
            "role_code": "estimator",
            "valid_from": today,
            "valid_to": tomorrow,
        },
        headers={**desk.heads["finance"], **idem()},
    )
    assert wrong_role.status_code == 422, wrong_role.text
    created = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["owner"]),
            "role_code": "finance_director",
            "valid_from": today,
            "valid_to": tomorrow,
        },
        headers={**desk.heads["finance"], **idem()},
    )
    assert created.status_code == 201, created.text
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200
    for position, who in ((1, "foreman"), (2, "estimator"), (3, "techdir")):
        assert (
            await decide(api, desk.heads[who], document, version, position, "approve")
        ).status_code == 200
    by_substitute = await decide(api, desk.heads["owner"], document, version, 4, "approve")
    assert by_substitute.status_code == 200, by_substitute.text
    decision = by_substitute.json()["decisions"][-1]
    assert decision["actor_name"] == "Владелец (dev)"
    assert decision["on_behalf_of_name"] == "Финансовый директор (dev)"
    assert by_substitute.json()["version_status"] == "approved"


async def test_the_author_cannot_decide_even_with_a_substitution_in_place(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    today = await local_today(worker_sync_engine)
    tomorrow = str(date.fromisoformat(today) + timedelta(days=1))
    delegated = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["estimator"]),
            "role_code": "technical_director",
            "valid_from": today,
            "valid_to": tomorrow,
        },
        headers={**desk.heads["techdir"], **idem()},
    )
    assert delegated.status_code == 201, delegated.text
    document, version = await other_version(api, s3, desk, "estimator")
    assert (await send(api, desk.heads["estimator"], document, version)).status_code == 200
    passed = await decide(api, desk.heads["foreman"], document, version, 1, "approve")
    assert passed.status_code == 200, passed.text
    author = await decide(api, desk.heads["estimator"], document, version, 2, "approve")
    assert author.status_code == 403, author.text
    assert author.json()["detail"] == "approval.author_forbidden"
    unchanged = await route_of(api, desk.heads["owner"], document, version)
    assert unchanged["current_position"] == 2


async def test_the_reason_of_a_money_rejection_is_visible_to_participants_only(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200
    rejected = await decide(
        api, desk.heads["foreman"], document, version, 1, "reject", reason="Объёмы не подтверждены"
    )
    assert rejected.status_code == 200, rejected.text
    seen_by_foreman = await route_of(api, desk.heads["foreman"], document, version)
    assert seen_by_foreman["decisions"][-1]["reason"] == "Объёмы не подтверждены"
    seen_by_author = await route_of(api, desk.heads["accountant"], document, version)
    assert seen_by_author["decisions"][-1]["reason"] == "Объёмы не подтверждены"
    seen_by_money = await route_of(api, desk.heads["finance"], document, version)
    assert seen_by_money["decisions"][-1]["reason"] == "Объёмы не подтверждены"
    outsider = await route_of(api, desk.heads["master"], document, version)
    assert outsider["decisions"][-1]["reason"] is None


async def test_a_non_money_reason_is_visible_to_document_viewers(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await other_version(api, s3, desk, "accountant")
    sent = await send(api, desk.heads["accountant"], document, version)
    assert sent.status_code == 200, sent.text
    assert [step["role_code"] for step in sent.json()["steps"]] == ["foreman", "technical_director"]
    rejected = await decide(
        api, desk.heads["foreman"], document, version, 1, "reject", reason="Нет подписи"
    )
    assert rejected.status_code == 200, rejected.text
    seen = await route_of(api, desk.heads["master"], document, version)
    assert seen["decisions"][-1]["reason"] == "Нет подписи"


async def test_the_queue_lists_the_documents_where_my_step_is_current(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200

    def mine(queue: list[dict[str, Any]]) -> set[str]:
        return {item["version_id"] for item in queue}

    foreman_queue = await api.get(f"{API}/documents/approvals", headers=desk.heads["foreman"])
    assert foreman_queue.status_code == 200, foreman_queue.text
    assert version in mine(foreman_queue.json())
    estimator_queue = await api.get(f"{API}/documents/approvals", headers=desk.heads["estimator"])
    assert version not in mine(estimator_queue.json())
    assert (
        await decide(api, desk.heads["foreman"], document, version, 1, "approve")
    ).status_code == 200
    estimator_after = await api.get(f"{API}/documents/approvals", headers=desk.heads["estimator"])
    assert version in mine(estimator_after.json())


async def test_a_signed_scan_becomes_the_effective_version(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    draft_scan = await _signed_scan(api, s3, desk, document, version, PDF_OK, "accountant")
    assert draft_scan.status_code == 409, draft_scan.text
    assert draft_scan.json()["detail"] == "document.parent_not_approved"
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200
    for position, who in ((1, "foreman"), (2, "estimator"), (3, "techdir")):
        assert (
            await decide(api, desk.heads[who], document, version, position, "approve")
        ).status_code == 200
    assert (
        await decide(api, desk.heads["finance"], document, version, 4, "approve")
    ).status_code == 200
    signed = await _signed_scan(api, s3, desk, document, version, PDF_OK, "accountant")
    assert signed.status_code == 201, signed.text
    body = signed.json()
    assert body["status"] == "signed"
    with worker_sync_engine.connect() as connection:
        previous = connection.execute(
            text("SELECT status FROM document_version WHERE id = CAST(:v AS uuid)"), {"v": version}
        ).scalar_one()
        parent = connection.execute(
            text("SELECT parent_version_id FROM document_version WHERE id = CAST(:s AS uuid)"),
            {"s": body["id"]},
        ).scalar_one()
        current = connection.execute(
            text("SELECT current_version_id FROM document WHERE id = CAST(:d AS uuid)"),
            {"d": document},
        ).scalar_one()
    assert previous == "archived"
    assert str(parent) == version
    assert str(current) == body["id"]


async def _signed_scan(
    api: AsyncClient,
    s3: httpx.AsyncClient,
    desk: Desk,
    document: str,
    version: str,
    content: bytes,
    who: str,
) -> httpx.Response:
    presigned = await api.post(
        f"{API}/documents/{document}/versions/presign",
        json={
            "content_type": PDF,
            "size_bytes": len(content),
            "checksum_sha256": sha256_b64(content),
        },
        headers=desk.heads[who],
    )
    assert presigned.status_code == 200, presigned.text
    item = presigned.json()
    stored = await s3.put(item["upload_url"], content=content, headers=item["headers"])
    assert stored.status_code == 200, stored.text
    return await api.post(
        f"{API}/documents/{document}/versions/{version}/signed",
        json={"key": item["key"], "comment": "подписанный скан"},
        headers={**desk.heads[who], **idem()},
    )


async def test_the_database_refuses_direct_writes_to_routes_decisions_and_reasons(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await money_version(api, s3, desk, "accountant", auth_world)
    assert (await send(api, desk.heads["accountant"], document, version)).status_code == 200
    rejected = await decide(
        api, desk.heads["foreman"], document, version, 1, "reject", reason="Объёмы не сходятся"
    )
    assert rejected.status_code == 200, rejected.text
    context = in_context(auth_world.users["estimator"], (desk.project,), (desk.legal,))
    refusals = [
        (
            "INSERT INTO approval_decision (document_id, version_id, legal_entity_id, project_id,"
            " attempt, position, role_code, decision, actor_id) VALUES (CAST(:d AS uuid),"
            " CAST(:v AS uuid), CAST(:le AS uuid), CAST(:p AS uuid), 1, 2, 'estimator', 'approve',"
            " CAST(:u AS uuid))",
            {
                "d": document,
                "v": version,
                "le": str(desk.legal),
                "p": str(desk.project),
                "u": str(auth_world.users["estimator"]),
            },
        ),
        (
            "UPDATE approval_decision SET decision = 'reject' WHERE version_id = CAST(:v AS uuid)",
            {"v": version},
        ),
        ("DELETE FROM approval_decision WHERE version_id = CAST(:v AS uuid)", {"v": version}),
        (
            "UPDATE version_route_step SET status = 'approved' WHERE version_id = CAST(:v AS uuid)",
            {"v": version},
        ),
        (
            "INSERT INTO document_delegation"
            " (delegator_id, delegate_id, role_code, valid_from, valid_to)"
            " VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 'estimator', CURRENT_DATE, CURRENT_DATE)",
            {"a": str(auth_world.users["estimator"]), "b": str(auth_world.users["owner"])},
        ),
        (
            "UPDATE document_delegation SET revoked_at = now()",
            {},
        ),
    ]
    for sql, params in refusals:
        with pytest.raises(DBAPIError, match=r"permission denied|approval\.|document_status_event"):
            await _write(sql, context, params)
    with pytest.raises(DBAPIError, match="permission denied"):
        await _write("SELECT reason FROM approval_decision", context, {})
    decision = {"d": await _decision_id(auth_world, desk, version)}
    participant_reason = await _scalar(
        "SELECT app.decision_reason(CAST(:d AS uuid))",
        in_context(auth_world.users["foreman"], (desk.project,), (desk.legal,)),
        decision,
    )
    assert participant_reason == "Объёмы не сходятся"
    outsider_reason = await _scalar(
        "SELECT app.decision_reason(CAST(:d AS uuid))",
        in_context(auth_world.users["master"], (desk.project,), (desk.legal,)),
        decision,
    )
    assert outsider_reason is None


async def _write(sql: str, context: Any, params: dict[str, Any]) -> None:
    with use_context(context):
        async with get_sessionmaker()() as session:
            await session.execute(text(sql), params)
            await session.commit()


async def _scalar(sql: str, context: Any, params: dict[str, Any]) -> Any:
    with use_context(context):
        async with get_sessionmaker()() as session:
            return (await session.execute(text(sql), params)).scalar_one()


async def _decision_id(auth_world: AuthWorld, desk: Desk, version: str) -> str:
    rows_found = await rows(
        "SELECT id FROM approval_decision WHERE version_id = CAST(:v AS uuid)",
        in_context(auth_world.users["owner"], (desk.project,), (desk.legal,)),
        {"v": version},
    )
    return str(rows_found[0][0])


async def test_a_signed_version_of_another_document_is_refused(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    _, version = await money_version(api, s3, desk, "accountant", auth_world)
    other_document, _ = await money_version(api, s3, desk, "accountant", auth_world)
    response = await _signed_scan(api, s3, desk, other_document, version, PDF_OK, "accountant")
    assert response.status_code == 404, response.text
