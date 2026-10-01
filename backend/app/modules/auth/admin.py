"""Смена роли и активности пользователя (вызывает администратор; экраны настроек появятся в S1c).

Право settings:admin проверяет сама функция БД по контексту запроса, поэтому сессия должна быть
открыта под пользователем-администратором (get_db), а не под системным контекстом.
"""

import uuid

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import denylist, repository
from app.modules.auth.errors import NotFound, PermissionDenied
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
