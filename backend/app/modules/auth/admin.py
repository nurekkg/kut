"""Администрирование пользователей: роль, активность, сброс 2FA, создание, ссылка активации.

Право settings:admin проверяет сама функция БД по контексту запроса, поэтому сессия должна быть
открыта под пользователем-администратором (get_db), а не под системным контекстом.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import denylist, repository
from app.modules.auth.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from app.modules.auth.principal import principal_store
from app.modules.auth.repository import RoleChange

INSUFFICIENT_PRIVILEGE = "42501"
NO_DATA_FOUND = "P0002"


def _translate(error: DBAPIError) -> Exception:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == INSUFFICIENT_PRIVILEGE:
        return PermissionDenied()
    if sqlstate == NO_DATA_FOUND:
        return NotFound()
    return error


async def _finish(session: AsyncSession, user_id: uuid.UUID, change: RoleChange) -> int:
    await session.commit()
    # Сначала БД (источник истины), затем Redis: токены со старым role_ver отклоняются сразу,
    # а access-токены отозванных сессий попадают в denylist.
    await denylist.deny(change.families)
    await principal_store.set_role_ver(user_id, change.role_ver)
    return change.role_ver


async def assign_role(session: AsyncSession, *, user_id: uuid.UUID, role_id: uuid.UUID) -> int:
    try:
        change = await repository.assign_role(session, user_id=user_id, role_id=role_id)
    except DBAPIError as error:
        await session.rollback()
        raise _translate(error) from error
    return await _finish(session, user_id, change)


async def set_user_active(session: AsyncSession, *, user_id: uuid.UUID, active: bool) -> int:
    try:
        change = await repository.set_user_active(session, user_id=user_id, active=active)
    except DBAPIError as error:
        await session.rollback()
        raise _translate(error) from error
    return await _finish(session, user_id, change)


# Имена ограничений user (миграции S1a): по ним клиенту сообщают, какое поле не подошло.
_UNIQUE_FIELDS = {"uq_user_email": "email", "uq_user_phone": "phone"}
_CHECK_CODES = {
    "ck_user_phone_e164": "invalid_phone",
    "ck_user_email_lowercase": "invalid_email",
    "ck_user_contact_required": "contact_required",
}


def _creation_error(error: DBAPIError) -> Exception:
    sqlstate = getattr(error.orig, "sqlstate", None)
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if sqlstate == "23505":
        return Conflict(_UNIQUE_FIELDS.get(constraint or "", "user"))
    if sqlstate == "23514":
        return InvalidInput(_CHECK_CODES.get(constraint or "", "invalid_counterparty"))
    return _translate(error)


async def create_user(
    session: AsyncSession,
    *,
    email: str | None,
    phone: str | None,
    full_name: str,
    role_id: uuid.UUID,
    counterparty_id: uuid.UUID | None,
    commit: bool = True,
) -> uuid.UUID:
    """Пользователь без пароля: войти можно только по ссылке активации (issue_invite).

    commit=False нужен вызывающему, у которого своя транзакция (идемпотентное создание).
    """
    try:
        user_id = await repository.admin_create_user(
            session,
            email=email,
            phone=phone,
            full_name=full_name,
            role_id=role_id,
            counterparty_id=counterparty_id,
        )
    except DBAPIError as error:
        await session.rollback()
        raise _creation_error(error) from error
    if commit:
        await session.commit()
    return user_id


async def reset_totp(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    try:
        change = await repository.reset_totp(session, user_id=user_id)
    except DBAPIError as error:
        await session.rollback()
        raise _translate(error) from error
    return await _finish(session, user_id, change)


async def issue_invite(
    session: AsyncSession, *, user_id: uuid.UUID, token_hash: bytes, ttl: timedelta
) -> datetime:
    try:
        expires = await repository.admin_issue_invite(
            session, user_id=user_id, token_hash=token_hash, ttl=ttl
        )
    except DBAPIError as error:
        await session.rollback()
        raise _translate(error) from error
    await session.commit()
    return expires
