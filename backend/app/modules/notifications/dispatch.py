import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.modules.documents.types import KINDS
from app.modules.notifications.adapters import NotificationAdapter, SendResult, mask_phone

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4  # первая попытка и три повтора
BACKOFF_SECONDS = (30, 120, 600)  # пауза после 1-й, 2-й и 3-й неудачной попытки
BATCH_SIZE = 10
LEASE = timedelta(minutes=10)  # строка занята воркером на время отправки; при сбое её заберут позже
TEMPLATE_WAIT = timedelta(hours=1)  # шаблон не одобрен или токен неверен: ждём, попытку не тратим
AUTH_STATUSES = frozenset({401, 403, 404})  # токен, номер или адрес сервиса: ждём исправления
# откладываний одной строки (около суток при паузе в час), потом строка уходит в failed
MAX_DEFERRALS = 24
COUNT_DEFERRALS = text(
    """
    SELECT count(*) FROM notification_delivery
    WHERE outbox_id = :id AND status = 'deferred'
    """
)

CLAIM = text(
    """
    SELECT o.id, o.template_code, o.params, o.attempts, u.phone, u.is_active,
           u.deleted_at, u.notify_whatsapp, t.wa_name, t.language, t.approval,
           t.params AS param_names
    FROM notification_outbox o
    JOIN "user" u ON u.id = o.recipient_user_id
    JOIN notification_template t ON t.code = o.template_code
    WHERE o.status = 'queued' AND o.next_attempt_at <= :now
      AND (CAST(:ids AS uuid[]) IS NULL OR o.id = ANY (CAST(:ids AS uuid[])))
    ORDER BY o.next_attempt_at, o.id
    LIMIT :batch
    FOR UPDATE OF o SKIP LOCKED
    """
)

LEASE_ROWS = text(
    """
    UPDATE notification_outbox
    SET next_attempt_at = CAST(:until AS timestamptz)
    WHERE id = ANY (CAST(:ids AS uuid[]))
    """
)

MARK_SENT = text(
    """
    UPDATE notification_outbox
    SET status = 'sent', attempts = :attempts, updated_at = now()
    WHERE id = :id
    """
)

UPDATE_OUTBOX = text(
    """
    UPDATE notification_outbox
    SET status = :status, attempts = :attempts,
        next_attempt_at = COALESCE(CAST(:next_at AS timestamptz), next_attempt_at),
        last_error_code = :error_code, updated_at = now()
    WHERE id = :id
    """
)

DEFER_OUTBOX = text(
    """
    UPDATE notification_outbox
    SET next_attempt_at = CAST(:next_at AS timestamptz), last_error_code = :error_code,
        updated_at = now()
    WHERE id = :id
    """
)

INSERT_DELIVERY = text(
    """
    INSERT INTO notification_delivery
        (outbox_id, attempt, status, provider_status, provider_message_id, error_code, phone_masked,
         defer_until)
    VALUES (:outbox_id, :attempt, :status, :provider_status, :message_id, :error_code, :phone,
            CAST(:defer_until AS timestamptz))
    """
)


def dispatch_due(
    engine: Engine,
    adapter: NotificationAdapter,
    *,
    base_url: str,
    now: datetime,
    batch: int = BATCH_SIZE,
    ids: list[UUID] | None = None,
) -> int:
    """Одна пачка очереди. Время передаётся параметром: повторы проверяются без реального ожидания.

    Строки арендуются короткой транзакцией, затем каждая отправляется и фиксируется отдельно:
    сбой записи одной строки не откатывает чужие и не останавливает пачку. Если провайдер принял
    сообщение, а результат не дошёл до COMMIT, строка вернётся после аренды и уйдёт повторно:
    WhatsApp Cloud API не принимает ключ идемпотентности, поэтому доставка at-least-once.
    ids ограничивает выборку (тесты); в работе не задан.
    """
    with engine.begin() as connection:
        rows = connection.execute(
            CLAIM,
            {"now": now, "batch": batch, "ids": [str(i) for i in ids] if ids is not None else None},
        ).all()
        if rows:
            connection.execute(
                LEASE_ROWS, {"until": now + LEASE, "ids": [str(row.id) for row in rows]}
            )
    for row in rows:
        accepted: list[bool] = []
        try:
            with engine.begin() as connection:
                _deliver(connection, adapter, row, base_url=base_url, now=now, accepted=accepted)
        except SQLAlchemyError as error:
            logger.warning("notification record failed: %s", type(error).__name__)
            if accepted:
                _mark_sent_without_journal(engine, row)
    return len(rows)


def _deliver(
    connection: Connection,
    adapter: NotificationAdapter,
    row: Any,
    *,
    base_url: str,
    now: datetime,
    accepted: list[bool],
) -> None:
    attempt = row.attempts + 1
    notifiable = row.is_active and row.deleted_at is None and row.notify_whatsapp
    if row.phone is None or not notifiable:
        _record(connection, row, attempt, "failed", "failed", "recipient.not_notifiable")
        return
    if adapter.disabled:
        _record(connection, row, attempt, "disabled", "disabled", "sending_disabled")
        return
    if adapter.requires_approved_template and row.approval != "approved":
        _defer(connection, row, now + TEMPLATE_WAIT, "template.not_approved")
        return
    try:
        params = render_params(row.param_names, row.params, base_url)
    except (KeyError, ValueError):
        _record(connection, row, attempt, "failed", "failed", "render.error")
        return
    result = _send(adapter, row, params)
    if result.ok:
        accepted.append(True)
        _record(connection, row, attempt, result.ok_status, "sent", result.error_code, result)
    elif result.provider_status in AUTH_STATUSES:
        _defer(connection, row, now + TEMPLATE_WAIT, result.error_code or "config")
    elif result.retryable and attempt < MAX_ATTEMPTS:
        next_at = now + timedelta(seconds=BACKOFF_SECONDS[attempt - 1])
        _record(connection, row, attempt, "retry", "queued", result.error_code, result, next_at)
    elif result.retryable:
        _record(connection, row, attempt, "dead", "dead", result.error_code, result)
    else:
        _record(connection, row, attempt, "failed", "failed", result.error_code, result)


def _mark_sent_without_journal(engine: Engine, row: Any) -> None:
    """Провайдер принял сообщение, а запись результата не прошла: строка закрыта без журнала,
    чтобы не отправить второй раз. Если и это не прошло, строка останется в очереди."""
    try:
        with engine.begin() as connection:
            connection.execute(MARK_SENT, {"id": row.id, "attempts": row.attempts + 1})
    except SQLAlchemyError as error:
        logger.error("notification mark sent failed: %s", type(error).__name__)


def _send(adapter: NotificationAdapter, row: Any, params: list[str]) -> SendResult:
    try:
        return adapter.send(
            to=row.phone, template=row.wa_name, language=row.language, params=params
        )
    except Exception as error:
        # Сбой адаптера (например, кодирование заголовка) - повторяемый исход, пачка не падает.
        # Текст исключения в лог не пишем: он может содержать фрагменты заголовков.
        logger.warning("notification adapter failed: %s", type(error).__name__)
        return SendResult(
            ok=False,
            retryable=True,
            ok_status="",
            provider_status=None,
            message_id=None,
            error_code="adapter_error",
        )


def _defer(connection: Connection, row: Any, next_at: datetime, error_code: str) -> None:
    deferred = connection.execute(COUNT_DEFERRALS, {"id": row.id}).scalar_one()
    if deferred >= MAX_DEFERRALS:
        _record(connection, row, row.attempts + 1, "failed", "failed", "deferred.expired")
        return
    connection.execute(DEFER_OUTBOX, {"id": row.id, "next_at": next_at, "error_code": error_code})
    connection.execute(
        INSERT_DELIVERY,
        {
            "outbox_id": row.id,
            "attempt": max(row.attempts, 1),
            "status": "deferred",
            "provider_status": None,
            "message_id": None,
            "error_code": error_code,
            "phone": mask_phone(row.phone),
            "defer_until": next_at.isoformat(),
        },
    )


def _record(
    connection: Connection,
    row: Any,
    attempt: int,
    delivery_status: str,
    outbox_status: str,
    error_code: str | None,
    result: SendResult | None = None,
    next_at: datetime | None = None,
) -> None:
    connection.execute(
        UPDATE_OUTBOX,
        {
            "id": row.id,
            "status": outbox_status,
            "attempts": attempt,
            "next_at": next_at,
            "error_code": error_code,
        },
    )
    connection.execute(
        INSERT_DELIVERY,
        {
            "outbox_id": row.id,
            "attempt": attempt,
            "status": delivery_status,
            "provider_status": result.provider_status if result else None,
            "message_id": result.message_id if result else None,
            "error_code": error_code,
            "phone": mask_phone(row.phone),
            "defer_until": None,
        },
    )


def render_params(names_csv: str, params: dict[str, Any], base_url: str) -> list[str]:
    """Параметры шаблона: код вида, номер, ссылка или дата. Имён, сумм и токенов здесь нет."""
    values = {
        "kind": _kind_code(params.get("kind")),
        "number": str(params.get("number", "")),
        "link": _link(params, base_url),
        "until": _date_ru(params.get("until")),
    }
    return [values[name] for name in names_csv.split(",")]


def _kind_code(kind: object) -> str:
    spec = KINDS.get(str(kind)) if kind is not None else None
    return spec.prefix if spec is not None else ""


def _link(params: dict[str, Any], base_url: str) -> str:
    if "document_id" in params:
        return f"{base_url}/documents/{params['document_id']}"
    return f"{base_url}/approvals"


def _date_ru(value: object) -> str:
    if value is None:
        return ""
    year, month, day = str(value).split("-")
    return f"{day}.{month}.{year}"
