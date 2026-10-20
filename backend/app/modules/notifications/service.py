from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

READ_OWN_FLAG = text(
    'SELECT notify_whatsapp, notify_whatsapp_consent_at, notify_whatsapp_revoked_at FROM "user"'
    " WHERE id = app.user_id()"
)

WRITE_OWN_FLAG = text(
    'UPDATE "user" SET notify_whatsapp = :value, updated_at = now() '
    "WHERE id = app.user_id() AND deleted_at IS NULL RETURNING id"
)


async def read_own_settings(session: AsyncSession) -> tuple[bool, datetime | None, datetime | None]:
    row = (await session.execute(READ_OWN_FLAG)).one_or_none()
    if row is None:
        return False, None, None
    return bool(row.notify_whatsapp), row.notify_whatsapp_consent_at, row.notify_whatsapp_revoked_at


async def write_own_flag(session: AsyncSession, value: bool) -> bool:
    """Меняет только свою строку (app.user_id()); дату согласия или отзыва ставит гард БД.
    False: строки нет или правка закрыта."""
    updated = (await session.execute(WRITE_OWN_FLAG, {"value": value})).first()
    if updated is None:
        return False
    await session.commit()
    return True
