import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Header, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.idempotency.models import IdempotencyKey

PENDING = 0  # response_code записи, которую выполняет первый запрос и которая ещё не завершена
REPLAY_HEADER = "Idempotent-Replayed"
LOCK_NOT_AVAILABLE = "55P03"

# Обязательный заголовок: без него или с не-UUID FastAPI отвечает 422.
IdempotencyKeyHeader = Annotated[uuid.UUID, Header(alias="Idempotency-Key")]


class KeyReused(HTTPException):
    """Тот же ключ с другим телом или адресом: это не повтор, а ошибка клиента (ADR-001 §5.1)."""

    def __init__(self) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, "idempotency_key_reused")


class InProgress(HTTPException):
    """Первый запрос с этим ключом ещё выполняется: клиент повторит позже."""

    def __init__(self) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT, "request_in_progress", headers={"Retry-After": "1"}
        )


@dataclass(frozen=True, slots=True)
class IdempotentResult:
    status_code: int
    body: Any
    replayed: bool

    def to_response(self) -> Response:
        headers = {REPLAY_HEADER: "true"} if self.replayed else None
        if self.body is None:
            return Response(status_code=self.status_code, headers=headers)
        return JSONResponse(self.body, status_code=self.status_code, headers=headers)


def fingerprint(method: str, path: str, payload: Any) -> bytes:
    """Хэш запроса: метод, путь и тело в каноническом JSON (порядок полей не важен)."""
    canonical = json.dumps(
        jsonable_encoder(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(f"{method.upper()}\n{path}\n{canonical}".encode()).digest()


def _is_lock_timeout(error: OperationalError) -> bool:
    return getattr(error.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE


async def _claim(
    session: AsyncSession, *, user_id: uuid.UUID, key: uuid.UUID, request_hash: bytes
) -> bool:
    settings = get_settings()
    await session.execute(
        text("SELECT set_config('lock_timeout', :value, true)"),
        {"value": f"{settings.idempotency_lock_timeout_ms}ms"},
    )
    expires_at = datetime.now(UTC) + timedelta(hours=settings.idempotency_ttl_hours)
    try:
        # При параллельном запросе с тем же ключом вставка ждёт конца первой транзакции:
        # после её COMMIT конфликт даёт 0 строк, после ROLLBACK ключ достаётся нам.
        claimed = await session.scalar(
            pg_insert(IdempotencyKey)
            .values(
                user_id=user_id,
                key=key,
                request_hash=request_hash,
                response_code=PENDING,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(IdempotencyKey.key)
        )
    except OperationalError as error:
        await session.rollback()
        if _is_lock_timeout(error):
            raise InProgress from error
        raise
    return claimed is not None


async def _replay(
    session: AsyncSession, *, user_id: uuid.UUID, key: uuid.UUID, request_hash: bytes
) -> IdempotentResult:
    row = (
        await session.execute(
            select(
                IdempotencyKey.request_hash,
                IdempotencyKey.response_code,
                IdempotencyKey.response_body,
            ).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
        )
    ).one_or_none()
    await session.rollback()  # записать нечего: освобождаем соединение
    if row is None or row.response_code == PENDING:
        raise InProgress
    if bytes(row.request_hash) != request_hash:
        raise KeyReused
    return IdempotentResult(status_code=row.response_code, body=row.response_body, replayed=True)


async def run_idempotent(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    key: uuid.UUID,
    request_hash: bytes,
    handler: Callable[[], Awaitable[tuple[int, Any]]],
) -> IdempotentResult:
    """Выполняет handler один раз на пару (user_id, key); повтор отдаёт сохранённый ответ.

    Запись ключа, бизнес-изменения handler и сохранённый ответ фиксируются одним COMMIT: упавший
    handler откатывает всё вместе с ключом, и тот же ключ можно использовать снова. Ответы-ошибки
    не сохраняются. Handler возвращает (код ответа, тело); тело приводится к JSON.
    """
    if not await _claim(session, user_id=user_id, key=key, request_hash=request_hash):
        return await _replay(session, user_id=user_id, key=key, request_hash=request_hash)
    try:
        status_code, raw_body = await handler()
        body = jsonable_encoder(raw_body)
        await session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
            .values(response_code=status_code, response_body=body)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return IdempotentResult(status_code=status_code, body=body, replayed=False)
