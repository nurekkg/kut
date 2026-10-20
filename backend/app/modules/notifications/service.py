from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Текст согласия, который показан в профиле. Версия пишется рядом с датой согласия (аудит).
CONSENT_TEXT_VERSION = "2026-10-20-v1"

READ_OWN_FLAG = text(
    "SELECT notify_whatsapp, notify_whatsapp_consent_at, notify_whatsapp_revoked_at,"
    ' notify_whatsapp_consent_version FROM "user" WHERE id = app.user_id()'
)

WRITE_OWN_FLAG = text(
    'UPDATE "user" SET notify_whatsapp = :value,'
    " notify_whatsapp_consent_version = CASE WHEN CAST(:value AS boolean)"
    " THEN CAST(:version AS text) ELSE notify_whatsapp_consent_version END, updated_at = now()"
    " WHERE id = app.user_id() AND deleted_at IS NULL RETURNING id"
)


async def read_own_settings(
    session: AsyncSession,
) -> tuple[bool, datetime | None, datetime | None, str | None]:
    row = (await session.execute(READ_OWN_FLAG)).one_or_none()
    if row is None:
        return False, None, None, None
    return (
        bool(row.notify_whatsapp),
        row.notify_whatsapp_consent_at,
        row.notify_whatsapp_revoked_at,
        row.notify_whatsapp_consent_version,
    )


async def write_own_flag(session: AsyncSession, value: bool, version: str | None) -> bool:
    """Меняет только свою строку (app.user_id()); дату согласия или отзыва ставит гард БД.
    Версия текста записывается при включении. False: строки нет или правка закрыта."""
    updated = (await session.execute(WRITE_OWN_FLAG, {"value": value, "version": version})).first()
    if updated is None:
        return False
    await session.commit()
    return True
