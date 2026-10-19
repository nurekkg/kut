"""Уведомления согласования через outbox (S6b, ADR-015 часть 6, DoD раздела 12).

Сеть к Meta не используется: WhatsAppCloudAdapter получает httpx.MockTransport. Повторы проверяются
фейковым временем, которое передаётся в dispatch_due параметром.
"""

import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.modules.auth import devseed
from app.modules.notifications.adapters import (
    LogNotificationAdapter,
    WhatsAppCloudAdapter,
    get_adapter,
    mask_phone,
    validate_notification_config,
)
from app.modules.notifications.dispatch import dispatch_due, render_params
from tests.auth_support import AuthWorld
from tests.documents_support import API, PDF, PDF_OK, Desk, create_document, open_desk, upload
from tests.equipment_support import idem, in_context, rows

MONEY_DESK = ("owner", "foreman", "estimator", "techdir", "finance", "master", "accountant")
TOKEN = "tok-SECRET-424242"
BASE_URL = "https://kut.example"


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def _money_version(
    api: AsyncClient, s3: httpx.AsyncClient, desk: Desk, auth_world: AuthWorld
) -> tuple[str, str]:
    created = await create_document(
        api,
        desk.heads["accountant"],
        {
            "kind": "ks2",
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "counterparty_id": str(auth_world.counterparties[devseed.CUSTOMER]),
            "title": "КС-2 для уведомлений",
        },
    )
    assert created.status_code == 201, created.text
    document = created.json()["id"]
    uploaded = await upload(api, s3, document, desk.heads["accountant"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    return document, uploaded.json()["id"]


async def _send(api: AsyncClient, desk: Desk, document: str, version: str) -> None:
    sent = await api.post(
        f"{API}/documents/{document}/versions/{version}/send",
        headers={**desk.heads["accountant"], **idem()},
    )
    assert sent.status_code == 200, sent.text


async def _decide(
    api: AsyncClient, headers: dict[str, str], document: str, version: str, position: int
) -> httpx.Response:
    return await api.post(
        f"{API}/documents/{document}/versions/{version}/decisions",
        json={"position": position, "decision": "approve"},
        headers={**headers, **idem()},
    )


def _outbox(engine: Engine, version: str, position: int | None = None) -> list[Any]:
    """Строки outbox документа версии (читает kut_worker: BYPASSRLS).

    position - только запросы шага: ключ req:<версия>:<попытка>:<позиция>:<пользователь>."""
    with engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT o.id, o.recipient_user_id, o.event_type, o.status, o.attempts,"
                " o.next_attempt_at, o.last_error_code, o.dedup_key"
                " FROM notification_outbox o"
                " JOIN document_version v ON v.document_id = o.document_id"
                " WHERE v.id = CAST(:v AS uuid) ORDER BY o.dedup_key"
            ),
            {"v": version},
        ).all()
    if position is None:
        return list(found)
    return [
        row
        for row in found
        if row.event_type == "approval_requested" and row.dedup_key.split(":")[3] == str(position)
    ]


def _deliveries(engine: Engine, outbox_id: UUID) -> list[Any]:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT attempt, status, error_code, phone_masked, provider_status"
                " FROM notification_delivery WHERE outbox_id = :o ORDER BY attempt"
            ),
            {"o": outbox_id},
        ).all()


def _local_today(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text("SELECT app.local_today()")).scalar_one())


def _adapter(transport: httpx.MockTransport, *, approved: bool = True) -> WhatsAppCloudAdapter:
    adapter = WhatsAppCloudAdapter(
        phone_number_id="1234567890",
        access_token=TOKEN,
        api_version="v21.0",
        transport=transport,
    )
    if approved:
        adapter.requires_approved_template = False
    return adapter


def _count_calls() -> tuple[list[httpx.Request], httpx.MockTransport]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"error": {"message": "boom"}})

    return calls, httpx.MockTransport(handler)


# --- тексты, параметры, маска ---------------------------------------------------------------------


async def test_every_template_text_is_free_of_names_sums_and_tokens(
    worker_sync_engine: Engine, auth_world: AuthWorld
) -> None:
    with worker_sync_engine.connect() as connection:
        templates = connection.execute(
            text("SELECT code, preview, params FROM notification_template")
        ).all()
        names = connection.execute(
            text('SELECT full_name FROM "user" WHERE full_name IS NOT NULL')
        ).all()
    assert len(templates) == 4
    for template in templates:
        without_placeholders = re.sub(r"\{\{\d\}\}", "", template.preview)
        assert not re.search(r"\d", without_placeholders), template.code
        assert "сом" not in template.preview.lower(), template.code
        assert "token" not in template.preview.lower(), template.code
        assert "токен" not in template.preview.lower(), template.code
        for (name,) in names:
            assert name not in template.preview, (template.code, name)


async def test_the_parameters_carry_the_kind_number_and_a_link_without_names(
    worker_sync_engine: Engine, auth_world: AuthWorld
) -> None:
    document = "5f0c3b7a-1111-4a2e-9c1d-2b3c4d5e6f70"
    values = render_params(
        "kind,number,link",
        {"kind": "ks2", "number": "КС2-ЮЛ1-2026-0006", "document_id": document},
        BASE_URL,
    )
    assert values == ["КС2", "КС2-ЮЛ1-2026-0006", f"{BASE_URL}/documents/{document}"]
    assert render_params("until,link", {"until": "2026-12-19"}, BASE_URL) == [
        "19.12.2026",
        f"{BASE_URL}/approvals",
    ]
    with worker_sync_engine.connect() as connection:
        names = connection.execute(text('SELECT full_name FROM "user"')).all()
    for value in values:
        assert all(name not in value for (name,) in names)


def test_the_journal_keeps_only_the_country_code_and_the_last_two_digits() -> None:
    assert mask_phone("+996555100001") == "+996 *** *** 01"
    assert mask_phone(None) == ""


# --- запись в транзакции события ------------------------------------------------------------------


async def test_a_sent_route_writes_one_row_per_step_holder_in_the_same_transaction(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    rows_found = _outbox(worker_sync_engine, version, position=1)
    assert len(rows_found) == 1, rows_found
    assert rows_found[0].recipient_user_id == auth_world.users["foreman"]
    assert rows_found[0].event_type == "approval_requested"
    assert rows_found[0].status == "queued"


async def test_a_decision_writes_the_next_step_and_a_rolled_back_decision_writes_nothing(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    before = len(_outbox(worker_sync_engine, version))
    # Решение в сессии без фиксации: строки outbox откатываются вместе с решением.
    context = in_context(auth_world.users["foreman"], (desk.project,), (desk.legal,))
    await rows(
        "SELECT app.route_decide(CAST(:v AS uuid), 1, 'approve', NULL)",
        context,
        {"v": version},
    )
    assert len(_outbox(worker_sync_engine, version)) == before
    decided = await _decide(api, desk.heads["foreman"], document, version, 1)
    assert decided.status_code == 200, decided.text
    after = _outbox(worker_sync_engine, version, position=2)
    assert {row.recipient_user_id for row in after} == {auth_world.users["estimator"]}


async def test_a_repeated_notification_of_the_same_step_adds_no_second_row(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    before = len(_outbox(worker_sync_engine, version))
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        connection.execute(text("SELECT app.notify_current_step(CAST(:v AS uuid))"), {"v": version})
    assert len(_outbox(worker_sync_engine, version)) == before


async def test_a_rejection_tells_the_author_once_and_a_full_approval_tells_the_author(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    assert (await _decide(api, desk.heads["foreman"], document, version, 1)).status_code == 200
    rejected = await api.post(
        f"{API}/documents/{document}/versions/{version}/decisions",
        json={"position": 2, "decision": "reject", "reason": "Не сходятся объёмы"},
        headers={**desk.heads["estimator"], **idem()},
    )
    assert rejected.status_code == 200, rejected.text
    found = _outbox(worker_sync_engine, version)
    told = [row for row in found if row.event_type == "approval_rejected"]
    assert [row.recipient_user_id for row in told] == [auth_world.users["accountant"]]
    assert told[0].dedup_key == f"rej:{version}:1:{auth_world.users['accountant']}"
    # Повторная отправка и согласование: подписчиком стал автор, шаги проходят по очереди.
    await _send(api, desk, document, version)
    for who, position in (("foreman", 1), ("estimator", 2), ("techdir", 3), ("finance", 4)):
        decided = await _decide(api, desk.heads[who], document, version, position)
        assert decided.status_code == 200, decided.text
    found = _outbox(worker_sync_engine, version)
    approved = [row for row in found if row.event_type == "approval_approved"]
    assert [row.recipient_user_id for row in approved] == [auth_world.users["accountant"]]


# --- замещение ------------------------------------------------------------------------------------


async def test_a_substitute_is_notified_of_the_step_and_of_the_delegation(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    """Замещающий с телефоном получает уведомление о замещении и о шаге за замещаемого."""
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    today = date.fromisoformat(_local_today(worker_sync_engine))
    delegated = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["master"]),
            "role_code": "technical_director",
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=3)),
        },
        headers={**desk.heads["techdir"], **idem()},
    )
    assert delegated.status_code == 201, delegated.text
    delegation_id = delegated.json()["id"]
    try:
        with worker_sync_engine.connect() as connection:
            notices = connection.execute(
                text(
                    "SELECT recipient_user_id, event_type FROM notification_outbox"
                    " WHERE dedup_key LIKE :p"
                ),
                {"p": f"deleg:{delegation_id}:%"},
            ).all()
        assert [(row.recipient_user_id, row.event_type) for row in notices] == [
            (auth_world.users["master"], "delegation_assigned")
        ]
        document, version = await _money_version(api, s3, desk, auth_world)
        await _send(api, desk, document, version)
        assert (await _decide(api, desk.heads["foreman"], document, version, 1)).status_code == 200
        assert (
            await _decide(api, desk.heads["estimator"], document, version, 2)
        ).status_code == 200
        third = _outbox(worker_sync_engine, version, position=3)
        assert {row.recipient_user_id for row in third} == {
            auth_world.users["techdir"],
            auth_world.users["master"],
        }
    finally:
        revoked = await api.post(
            f"{API}/documents/delegations/{delegation_id}/revoke",
            headers={**desk.heads["techdir"], **idem()},
        )
        assert revoked.status_code == 204, revoked.text


async def test_a_user_who_switched_whatsapp_off_gets_no_row(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET notify_whatsapp = false WHERE id = CAST(:u AS uuid)'),
            {"u": str(auth_world.users["foreman"])},
        )
    try:
        document, version = await _money_version(api, s3, desk, auth_world)
        await _send(api, desk, document, version)
        assert _outbox(worker_sync_engine, version) == []
    finally:
        with worker_sync_engine.begin() as connection:
            connection.execute(
                text('UPDATE "user" SET notify_whatsapp = true WHERE id = CAST(:u AS uuid)'),
                {"u": str(auth_world.users["foreman"])},
            )


async def test_the_settings_switch_is_refused_to_the_portal(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "owner", "customer")
    read = await api.get(f"{API}/notifications/settings", headers=desk.heads["customer"])
    assert read.status_code == 200, read.text
    assert read.json()["can_notify"] is False
    changed = await api.patch(
        f"{API}/notifications/settings",
        json={"notify_whatsapp": False},
        headers={**desk.heads["customer"], **idem()},
    )
    assert changed.status_code == 403, changed.text


async def test_the_owner_switches_own_notifications_off_and_on_again(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "foreman")
    off = await api.patch(
        f"{API}/notifications/settings",
        json={"notify_whatsapp": False},
        headers={**desk.heads["foreman"], **idem()},
    )
    assert off.status_code == 200, off.text
    assert off.json() == {"notify_whatsapp": False, "can_notify": True}
    on = await api.patch(
        f"{API}/notifications/settings",
        json={"notify_whatsapp": True},
        headers={**desk.heads["foreman"], **idem()},
    )
    assert on.json()["notify_whatsapp"] is True


# --- отправка, повторы, журнал --------------------------------------------------------------------


async def test_a_server_error_is_retried_three_times_with_growing_pauses_then_dead(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)
    calls, transport = _count_calls()
    adapter = _adapter(transport)
    start = datetime.now(UTC) + timedelta(minutes=5)
    ids = [queued.id]

    def run(at: datetime) -> int:
        return dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=at, ids=ids)

    assert run(start) == 1
    assert _state(worker_sync_engine, queued.id) == ("queued", 1, start + timedelta(seconds=30))
    assert run(start + timedelta(seconds=29)) == 0
    assert run(start + timedelta(seconds=30)) == 1
    assert _state(worker_sync_engine, queued.id) == (
        "queued",
        2,
        start + timedelta(seconds=150),
    )
    assert run(start + timedelta(seconds=149)) == 0
    assert run(start + timedelta(seconds=150)) == 1
    assert _state(worker_sync_engine, queued.id) == (
        "queued",
        3,
        start + timedelta(seconds=750),
    )
    assert run(start + timedelta(seconds=750)) == 1
    assert _state(worker_sync_engine, queued.id)[:2] == ("dead", 4)
    assert len(calls) == 4
    assert [row.status for row in _deliveries(worker_sync_engine, queued.id)] == [
        "retry",
        "retry",
        "retry",
        "dead",
    ]
    assert run(start + timedelta(hours=2)) == 0
    assert len(calls) == 4


def _state(engine: Engine, outbox_id: UUID) -> tuple[str, int, datetime]:
    with engine.connect() as connection:
        status, attempts, next_at = connection.execute(
            text("SELECT status, attempts, next_attempt_at FROM notification_outbox WHERE id = :i"),
            {"i": outbox_id},
        ).one()
    return status, attempts, next_at


async def test_a_client_error_is_failed_at_once_without_a_retry(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "bad number"}})

    adapter = _adapter(httpx.MockTransport(handler))
    start = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=start, ids=[queued.id])
        == 1
    )
    assert _state(worker_sync_engine, queued.id)[:2] == ("failed", 1)
    assert [row.status for row in _deliveries(worker_sync_engine, queued.id)] == ["failed"]
    assert (
        dispatch_due(
            worker_sync_engine,
            adapter,
            base_url=BASE_URL,
            now=start + timedelta(days=1),
            ids=[queued.id],
        )
        == 0
    )
    assert len(calls) == 1


async def test_an_unapproved_template_is_not_sent_at_all(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)
    calls, transport = _count_calls()
    adapter = _adapter(transport, approved=False)
    start = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=start, ids=[queued.id])
        == 1
    )
    assert calls == []
    (delivery,) = _deliveries(worker_sync_engine, queued.id)
    assert (delivery.status, delivery.error_code) == ("failed", "template.not_approved")


async def test_the_dev_adapter_records_the_step_as_logged_without_network(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)
    start = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        dispatch_due(
            worker_sync_engine,
            LogNotificationAdapter(),
            base_url=BASE_URL,
            now=start,
            ids=[queued.id],
        )
        == 1
    )
    assert _state(worker_sync_engine, queued.id)[:2] == ("sent", 1)
    (delivery,) = _deliveries(worker_sync_engine, queued.id)
    assert (delivery.status, delivery.phone_masked) == ("logged", "+996 *** *** 01")


def test_the_dev_settings_choose_the_log_adapter() -> None:
    settings = get_settings()
    assert settings.whatsapp_enabled is False
    assert isinstance(get_adapter(settings), LogNotificationAdapter)


def test_enabled_without_configuration_is_refused_at_start() -> None:
    settings = get_settings().model_copy(
        update={"whatsapp_enabled": True, "whatsapp_phone_number_id": None}
    )
    with pytest.raises(RuntimeError, match="WHATSAPP_PHONE_NUMBER_ID"):
        validate_notification_config(settings)


async def test_the_token_never_reaches_the_log_or_the_journal(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)
    calls, transport = _count_calls()
    adapter = _adapter(transport)
    start = datetime.now(UTC) + timedelta(minutes=5)
    with caplog.at_level(logging.DEBUG):
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=start, ids=[queued.id])
    assert len(calls) == 1
    assert calls[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in caplog.text
    with worker_sync_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT string_agg(coalesce(error_code, '') || ' ' || coalesce(phone_masked, '')"
                " || ' ' || coalesce(provider_message_id, ''), ' ') FROM notification_delivery"
                " WHERE outbox_id = :o"
            ),
            {"o": queued.id},
        ).scalar_one()
    assert TOKEN not in stored
    assert "+996555100001" not in stored


async def test_the_journal_is_refused_to_everyone_but_the_owner(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "owner", "foreman", "estimator")
    owner = await api.get(f"{API}/notifications/deliveries", headers=desk.heads["owner"])
    assert owner.status_code == 200, owner.text
    for who in ("foreman", "estimator"):
        refused = await api.get(f"{API}/notifications/deliveries", headers=desk.heads[who])
        assert refused.status_code == 403, (who, refused.text)


def test_the_delivery_journal_is_append_only(
    worker_sync_engine: Engine, owner_sync_engine: Engine
) -> None:
    """Воркер пишет только INSERT: UPDATE и DELETE отказаны правами. Триггер - в каталоге."""
    with owner_sync_engine.connect() as connection:
        trigger = connection.execute(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'notification_delivery_append_only'"
            )
        ).scalar_one()
    assert trigger == 1
    for statement in (
        "UPDATE notification_delivery SET status = 'sent'",
        "DELETE FROM notification_delivery",
    ):
        with (
            pytest.raises(DBAPIError, match="permission denied"),
            worker_sync_engine.begin() as connection,
        ):
            connection.execute(text(statement))
