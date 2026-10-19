from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

READ_OWN_FLAG = text('SELECT notify_whatsapp FROM "user" WHERE id = app.user_id()')

WRITE_OWN_FLAG = text(
    'UPDATE "user" SET notify_whatsapp = :value, updated_at = now() '
    "WHERE id = app.user_id() AND deleted_at IS NULL RETURNING id"
)


async def read_own_flag(session: AsyncSession) -> bool:
    return bool(await session.scalar(READ_OWN_FLAG))


async def write_own_flag(session: AsyncSession, value: bool) -> bool:
    """Меняет только свою строку (app.user_id()). False: строки нет или правка закрыта."""
    updated = (await session.execute(WRITE_OWN_FLAG, {"value": value})).first()
    if updated is None:
        return False
    await session.commit()
    return True
