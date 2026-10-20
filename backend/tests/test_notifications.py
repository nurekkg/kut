"""Уведомления согласования через outbox (S6b, ADR-015 часть 6, DoD раздела 12).

Сеть к Meta не используется: WhatsAppCloudAdapter получает httpx.MockTransport. Повторы проверяются
фейковым временем, которое передаётся в dispatch_due параметром.
"""

import logging
import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

import app.modules.notifications.tasks  # noqa: F401
from app.core.config import get_settings
from app.modules.auth import devseed
from app.modules.notifications.adapters import (
    DisabledNotificationAdapter,
    LogNotificationAdapter,
    SendResult,
    WhatsAppCloudAdapter,
    get_adapter,
    mask_phone,
    validate_notification_config,
)
from app.modules.notifications.dispatch import dispatch_due, render_params
from tests.auth_support import AuthWorld
from tests.documents_support import (
    API,
    PDF,
    PDF_OK,
    Desk,
    create_document,
    leave_no_access_behind,  # noqa: F401
    open_desk,
    upload,
)
from tests.equipment_support import idem, in_context, rows

MONEY_DESK = ("owner", "foreman", "estimator", "techdir", "finance", "master", "accountant")
TOKEN = "tok-SECRET-424242"
BASE_URL = "https://kut.example"


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


# В сиде телефон есть у прораба и мастера. Роли шагов получают тестовые телефоны на время теста.
STEP_PHONES = {
    "estimator": "+996700000002",
    "techdir": "+996700000003",
    "finance": "+996700000004",
    "accountant": "+996700000005",
}


# WhatsApp по умолчанию выключен (S6b-close). Прораб и роли шагов дают согласие на время теста: флаг
# ставит тот же гард БД, что и при включении в профиле (дата согласия пишется в строку).
CONSENTED = ("foreman", *STEP_PHONES)


@pytest.fixture(autouse=True)
def step_roles_have_phones(worker_sync_engine: Engine, auth_world: AuthWorld) -> Iterator[None]:
    def set_phones(phones: dict[str, str | None]) -> None:
        with worker_sync_engine.begin() as connection:
            for login, phone in phones.items():
                connection.execute(
                    text('UPDATE "user" SET phone = :p WHERE id = CAST(:u AS uuid)'),
                    {"p": phone, "u": str(auth_world.users[login])},
                )

    def set_consent(flag: bool) -> None:
        with worker_sync_engine.begin() as connection:
            for login in CONSENTED:
                connection.execute(
                    text('UPDATE "user" SET notify_whatsapp = :f WHERE id = CAST(:u AS uuid)'),
                    {"f": flag, "u": str(auth_world.users[login])},
                )

    set_phones(dict(STEP_PHONES))
    set_consent(True)
    try:
        yield
    finally:
        set_consent(False)
        set_phones({login: None for login in STEP_PHONES})


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


def _last_error(engine: Engine, outbox_id: UUID) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT last_error_code FROM notification_outbox WHERE id = :i"),
            {"i": outbox_id},
        ).scalar_one()


async def _revoke_own_delegations(api: AsyncClient, headers: dict[str, str], role: str) -> None:
    """Снимает свои действующие замещения по роли: пересекающиеся записи мешают другим тестам."""
    listed = await api.get(f"{API}/documents/delegations", headers=headers)
    assert listed.status_code == 200, listed.text
    for row in listed.json():
        if row["role_code"] == role and not row["revoked"]:
            revoked = await api.post(
                f"{API}/documents/delegations/{row['id']}/revoke",
                headers={**headers, **idem()},
            )
            assert revoked.status_code == 204, revoked.text


def _deliveries(engine: Engine, outbox_id: UUID) -> list[Any]:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT attempt, status, error_code, phone_masked, provider_status, defer_until"
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
    """Замещающий с правом согласования и телефоном получает уведомление о замещении и о шаге."""
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    today = date.fromisoformat(_local_today(worker_sync_engine))
    await _revoke_own_delegations(api, desk.heads["techdir"], "technical_director")
    delegated = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["finance"]),
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
                {"p": f"deleg:{auth_world.users['finance']}:%"},
            ).all()
        assert [(row.recipient_user_id, row.event_type) for row in notices] == [
            (auth_world.users["finance"], "delegation_assigned")
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
            auth_world.users["finance"],
        }
    finally:
        revoked = await api.post(
            f"{API}/documents/delegations/{delegation_id}/revoke",
            headers={**desk.heads["techdir"], **idem()},
        )
        assert revoked.status_code == 204, revoked.text


async def test_a_delegate_without_the_approval_right_gets_no_notice(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    """Замещение разрешено, но сообщение получает только тот, кто может согласовать."""
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    today = date.fromisoformat(_local_today(worker_sync_engine))
    delegated = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["master"]),
            "role_code": "technical_director",
            "valid_from": str(today + timedelta(days=300)),
            "valid_to": str(today + timedelta(days=301)),
        },
        headers={**desk.heads["techdir"], **idem()},
    )
    assert delegated.status_code == 201, delegated.text
    try:
        with worker_sync_engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM notification_outbox WHERE dedup_key LIKE :p"),
                {"p": f"deleg:{auth_world.users['master']}:%"},
            ).scalar_one()
        assert count == 0
    finally:
        revoked = await api.post(
            f"{API}/documents/delegations/{delegated.json()['id']}/revoke",
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


async def test_the_user_gives_and_withdraws_consent_and_the_dates_are_kept(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    """Согласие: включает сам сотрудник, дату согласия ставит гард; отзыв ставит дату отзыва."""
    desk = await open_desk(api, auth_world, worker_sync_engine, "foreman")
    start = datetime.now(UTC) - timedelta(seconds=1)
    try:
        off = await api.patch(
            f"{API}/notifications/settings",
            json={"notify_whatsapp": False},
            headers={**desk.heads["foreman"], **idem()},
        )
        assert off.status_code == 200, off.text
        withdrawn = off.json()
        assert withdrawn["notify_whatsapp"] is False
        assert withdrawn["can_notify"] is True
        assert datetime.fromisoformat(withdrawn["revoked_at"]) >= start
    finally:
        on = await api.patch(
            f"{API}/notifications/settings",
            json={"notify_whatsapp": True},
            headers={**desk.heads["foreman"], **idem()},
        )
        assert on.status_code == 200, on.text
        assert on.json()["notify_whatsapp"] is True
        assert datetime.fromisoformat(on.json()["consent_at"]) >= start


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


async def test_an_unapproved_template_waits_without_spending_an_attempt(
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
    assert _state(worker_sync_engine, queued.id) == ("queued", 0, start + timedelta(hours=1))
    assert _last_error(worker_sync_engine, queued.id) == "template.not_approved"
    (deferred,) = _deliveries(worker_sync_engine, queued.id)
    assert deferred.status == "deferred"
    assert deferred.error_code == "template.not_approved"
    assert deferred.defer_until == start + timedelta(hours=1)


class _BrokenAdapter:
    """Адаптер, который падает до отправки (как кодирование заголовка с нелатинским токеном)."""

    requires_approved_template = False
    disabled = False

    def send(self, *, to: str, template: str, language: str, params: list[str]) -> SendResult:
        raise UnicodeEncodeError("ascii", "токен", 0, 1, "boom")


async def test_an_adapter_error_is_retried_and_does_not_stop_the_batch(
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
    broken = _BrokenAdapter()
    assert (
        dispatch_due(worker_sync_engine, broken, base_url=BASE_URL, now=start, ids=[queued.id]) == 1
    )
    assert _state(worker_sync_engine, queued.id)[:2] == ("queued", 1)
    assert _last_error(worker_sync_engine, queued.id) == "adapter_error"


async def test_a_failed_record_closes_the_row_and_never_sends_it_twice(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой записи результата откатывает только эту строку: она остаётся в очереди после аренды."""
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)

    def broken_record(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("запись результата не удалась")

    monkeypatch.setattr("app.modules.notifications.dispatch._record", broken_record)
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
    assert _deliveries(worker_sync_engine, queued.id) == []


async def test_an_answer_without_a_message_id_is_sent_and_never_repeated(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    adapter = _adapter(httpx.MockTransport(handler))
    start = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=start, ids=[queued.id])
        == 1
    )
    assert _state(worker_sync_engine, queued.id)[:2] == ("sent", 1)
    assert _last_error(worker_sync_engine, queued.id) == "bad_response"
    later = start + timedelta(days=1)
    assert (
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=later, ids=[queued.id])
        == 0
    )


def test_the_template_approval_is_an_operator_function(
    owner_sync_engine: Engine, worker_sync_engine: Engine
) -> None:
    with owner_sync_engine.begin() as connection:
        changed = connection.execute(
            text("SELECT app.notification_template_approve('approval_requested', true)")
        ).scalar_one()
    try:
        assert changed == 1
        with worker_sync_engine.connect() as connection:
            approval = connection.execute(
                text("SELECT approval FROM notification_template WHERE code = 'approval_requested'")
            ).scalar_one()
        assert approval == "approved"
    finally:
        with owner_sync_engine.begin() as connection:
            connection.execute(
                text("SELECT app.notification_template_approve('approval_requested', false)")
            )


def test_an_empty_token_counts_as_missing_at_start() -> None:
    settings = get_settings().model_copy(
        update={
            "whatsapp_enabled": True,
            "whatsapp_phone_number_id": "1234567890",
            "whatsapp_access_token": SecretStr(""),
        }
    )
    with pytest.raises(RuntimeError, match="WHATSAPP_ACCESS_TOKEN"):
        validate_notification_config(settings)


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


async def test_a_delegate_without_the_approval_right_gets_no_request(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    """Запрос согласования не уходит делегату без права согласования (раскрытие вида документа)."""
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    today = date.fromisoformat(_local_today(worker_sync_engine))
    delegated = await api.post(
        f"{API}/documents/delegations",
        json={
            "delegate_id": str(auth_world.users["master"]),
            "role_code": "estimator",
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=1)),
        },
        headers={**desk.heads["estimator"], **idem()},
    )
    assert delegated.status_code == 201, delegated.text
    try:
        document, version = await _money_version(api, s3, desk, auth_world)
        await _send(api, desk, document, version)
        assert (await _decide(api, desk.heads["foreman"], document, version, 1)).status_code == 200
        second = _outbox(worker_sync_engine, version, position=2)
        assert {row.recipient_user_id for row in second} == {auth_world.users["estimator"]}
    finally:
        await _revoke_own_delegations(api, desk.heads["estimator"], "estimator")


async def test_another_user_cannot_switch_my_whatsapp_flag(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    """Переключатель уведомлений меняется только у себя, даже из SQL-сессии приложения."""
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    context = in_context(auth_world.users["foreman"], (desk.project,), (desk.legal,))
    master = {"u": str(auth_world.users["master"])}
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET notify_whatsapp = true WHERE id = CAST(:u AS uuid)'), master
        )
    try:
        with pytest.raises(DBAPIError, match=r"user\.notify_forbidden"):
            await rows(
                'UPDATE "user" SET notify_whatsapp = false WHERE id = CAST(:u AS uuid)',
                context,
                master,
            )
    finally:
        with worker_sync_engine.begin() as connection:
            connection.execute(
                text('UPDATE "user" SET notify_whatsapp = false WHERE id = CAST(:u AS uuid)'),
                master,
            )


async def test_an_expired_token_waits_without_losing_the_message(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    """Истёкший токен (401) не переводит строку в failed: сообщение ждёт исправления."""
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "expired"}})

    adapter = _adapter(httpx.MockTransport(handler))
    start = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=start, ids=[queued.id])
        == 1
    )
    assert _state(worker_sync_engine, queued.id) == ("queued", 0, start + timedelta(hours=1))
    assert _last_error(worker_sync_engine, queued.id) == "http_401"
    (deferred,) = _deliveries(worker_sync_engine, queued.id)
    assert deferred.status == "deferred"
    assert deferred.defer_until == start + timedelta(hours=1)


def test_whatsapp_is_off_by_default_and_no_colleague_consented_by_accident(
    worker_sync_engine: Engine, auth_world: AuthWorld
) -> None:
    with worker_sync_engine.connect() as connection:
        default = connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = 'user'"
                " AND column_name = 'notify_whatsapp'"
            )
        ).scalar_one()
        master = connection.execute(
            text(
                'SELECT notify_whatsapp, notify_whatsapp_consent_at FROM "user"'
                " WHERE id = CAST(:u AS uuid)"
            ),
            {"u": str(auth_world.users["master"])},
        ).one()
    assert default == "false"
    assert master.notify_whatsapp is False


async def test_another_user_cannot_enable_my_whatsapp_flag(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    context = in_context(auth_world.users["foreman"], (desk.project,), (desk.legal,))
    with pytest.raises(DBAPIError, match=r"user\.notify_forbidden"):
        await rows(
            'UPDATE "user" SET notify_whatsapp = true WHERE id = CAST(:u AS uuid)',
            context,
            {"u": str(auth_world.users["master"])},
        )


async def test_a_withdrawn_consent_creates_no_new_row(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    withdrawn = await api.patch(
        f"{API}/notifications/settings",
        json={"notify_whatsapp": False},
        headers={**desk.heads["foreman"], **idem()},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    try:
        document, version = await _money_version(api, s3, desk, auth_world)
        await _send(api, desk, document, version)
        assert _outbox(worker_sync_engine, version, position=1) == []
    finally:
        restored = await api.patch(
            f"{API}/notifications/settings",
            json={"notify_whatsapp": True},
            headers={**desk.heads["foreman"], **idem()},
        )
        assert restored.status_code == 200, restored.text


async def test_the_owner_switches_a_colleague_whatsapp_off_and_the_date_stays(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    foreman = str(auth_world.users["foreman"])
    refused = await api.post(
        f"{API}/settings/users/{foreman}/whatsapp-off", headers=desk.heads["foreman"]
    )
    assert refused.status_code == 403, refused.text
    missing = await api.post(
        f"{API}/settings/users/{UUID(int=1)}/whatsapp-off", headers=desk.heads["owner"]
    )
    assert missing.status_code == 404, missing.text
    try:
        off = await api.post(
            f"{API}/settings/users/{foreman}/whatsapp-off", headers=desk.heads["owner"]
        )
        assert off.status_code == 204, off.text
        with worker_sync_engine.connect() as connection:
            row = connection.execute(
                text(
                    'SELECT notify_whatsapp, notify_whatsapp_revoked_at FROM "user"'
                    " WHERE id = CAST(:u AS uuid)"
                ),
                {"u": foreman},
            ).one()
        assert row.notify_whatsapp is False
        assert row.notify_whatsapp_revoked_at is not None
    finally:
        with worker_sync_engine.begin() as connection:
            connection.execute(
                text('UPDATE "user" SET notify_whatsapp = true WHERE id = CAST(:u AS uuid)'),
                {"u": foreman},
            )


async def test_in_production_without_whatsapp_the_row_is_disabled_not_logged(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, *MONEY_DESK)
    document, version = await _money_version(api, s3, desk, auth_world)
    await _send(api, desk, document, version)
    (queued,) = _outbox(worker_sync_engine, version, position=1)
    production = get_settings().model_copy(
        update={"app_env": "production", "whatsapp_enabled": False}
    )
    adapter = get_adapter(production)
    assert isinstance(adapter, DisabledNotificationAdapter)
    assert isinstance(get_adapter(get_settings()), LogNotificationAdapter)
    start = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=start, ids=[queued.id])
        == 1
    )
    assert _state(worker_sync_engine, queued.id)[0] == "disabled"
    (delivery,) = _deliveries(worker_sync_engine, queued.id)
    assert delivery.status == "disabled"
    assert delivery.error_code == "sending_disabled"


async def test_a_token_with_non_ascii_characters_never_reaches_the_log(
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
    token = "токен-тест-α-SECRET-777"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": [{"id": "wamid.TEST"}]})

    adapter = WhatsAppCloudAdapter(
        phone_number_id="1234567890",
        access_token=token,
        api_version="v21.0",
        transport=httpx.MockTransport(handler),
    )
    adapter.requires_approved_template = False
    caplog.set_level(logging.DEBUG)
    start = datetime.now(UTC) + timedelta(minutes=5)
    assert (
        dispatch_due(worker_sync_engine, adapter, base_url=BASE_URL, now=start, ids=[queued.id])
        == 1
    )
    assert token not in caplog.text
    assert "SECRET-777" not in caplog.text
    assert "SECRET-777" not in repr(_deliveries(worker_sync_engine, queued.id))


async def test_the_delivery_journal_refuses_an_update_even_for_a_role_with_the_privilege(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
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
    with (
        pytest.raises(DBAPIError, match=r"notification\.append_only"),
        owner_sync_engine.begin() as connection,
    ):
        connection.execute(text("GRANT SELECT, UPDATE ON notification_delivery TO kut_auth"))
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        connection.execute(
            text(
                "UPDATE notification_delivery SET status = status"
                " WHERE outbox_id = CAST(:o AS uuid)"
            ),
            {"o": str(queued.id)},
        )
    with worker_sync_engine.connect() as connection:
        granted = connection.execute(
            text("SELECT has_table_privilege('kut_auth', 'notification_delivery', 'UPDATE')")
        ).scalar_one()
    assert granted is False
