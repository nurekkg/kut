from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.modules.documents.types import KINDS
from app.modules.notifications.adapters import NotificationAdapter, SendResult, mask_phone

MAX_ATTEMPTS = 4  # первая попытка и три повтора
BACKOFF_SECONDS = (30, 120, 600)  # пауза после 1-й, 2-й и 3-й неудачной попытки
BATCH_SIZE = 20

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

UPDATE_OUTBOX = text(
    """
    UPDATE notification_outbox
    SET status = :status, attempts = :attempts,
        next_attempt_at = COALESCE(CAST(:next_at AS timestamptz), next_attempt_at),
        last_error_code = :error_code, updated_at = now()
    WHERE id = :id
    """
)

INSERT_DELIVERY = text(
    """
    INSERT INTO notification_delivery
        (outbox_id, attempt, status, provider_status, provider_message_id, error_code, phone_masked)
    VALUES (:outbox_id, :attempt, :status, :provider_status, :message_id, :error_code, :phone)
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

    ids ограничивает выборку (тесты); в работе не задан.
    """
    with engine.begin() as connection:
        rows = connection.execute(
            CLAIM, {"now": now, "batch": batch, "ids": [str(i) for i in ids] if ids else None}
        ).all()
        for row in rows:
            _deliver(connection, adapter, row, base_url=base_url, now=now)
    return len(rows)


def _deliver(
    connection: Connection,
    adapter: NotificationAdapter,
    row: Any,
    *,
    base_url: str,
    now: datetime,
) -> None:
    attempt = row.attempts + 1
    notifiable = row.is_active and row.deleted_at is None and row.notify_whatsapp
    if row.phone is None or not notifiable:
        _record(connection, row, attempt, "failed", "failed", "recipient.not_notifiable")
        return
    if adapter.requires_approved_template and row.approval != "approved":
        _record(connection, row, attempt, "failed", "failed", "template.not_approved")
        return
    params = render_params(row.param_names, row.params, base_url)
    result = adapter.send(
        to=row.phone, template=row.wa_name, language=row.language, params=params
    )
    if result.ok:
        _record(connection, row, attempt, result.ok_status, "sent", None, result)
    elif result.retryable and attempt < MAX_ATTEMPTS:
        next_at = now + timedelta(seconds=BACKOFF_SECONDS[attempt - 1])
        _record(connection, row, attempt, "retry", "queued", result.error_code, result, next_at)
    elif result.retryable:
        _record(connection, row, attempt, "dead", "dead", result.error_code, result)
    else:
        _record(connection, row, attempt, "failed", "failed", result.error_code, result)


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
            "phone": _masked(row),
        },
    )


def _masked(row: Any) -> str:
    return mask_phone(row.phone)


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

