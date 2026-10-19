from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, get_principal, require
from app.modules.auth.principal import Principal
from app.modules.notifications import service
from app.modules.notifications.schemas import DeliveryOut, NotifySettingsIn, NotifySettingsOut
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/notifications", tags=["notifications"])

Db = Annotated[AsyncSession, Depends(get_db)]
Journal = Annotated[Principal, Depends(require(Module.NOTIFICATIONS, Action.READ))]
Me = Annotated[Principal, Depends(get_principal)]

DELIVERIES = text(
    """
    SELECT d.id, d.created_at, o.event_type, o.template_code, d.attempt, d.status,
           d.error_code, d.phone_masked
    FROM notification_delivery d
    JOIN notification_outbox o ON o.id = d.outbox_id
    ORDER BY d.created_at DESC, d.id DESC
    LIMIT :limit
    """
)


@router.get("/deliveries", response_model=list[DeliveryOut])
async def deliveries(
    session: Db,
    _principal: Journal,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[DeliveryOut]:
    rows = (await session.execute(DELIVERIES, {"limit": limit})).all()
    return [DeliveryOut.model_validate(row._asdict()) for row in rows]


@router.get("/settings", response_model=NotifySettingsOut)
async def read_settings(session: Db, principal: Me) -> NotifySettingsOut:
    # Портал не читает таблицу user: у его логина нет доступа к колонке, и уведомлений ему нет.
    if not principal.is_internal:
        return NotifySettingsOut(notify_whatsapp=False, can_notify=False)
    return NotifySettingsOut(notify_whatsapp=await service.read_own_flag(session), can_notify=True)


@router.patch("/settings", response_model=NotifySettingsOut)
async def update_settings(body: NotifySettingsIn, session: Db, principal: Me) -> NotifySettingsOut:
    if not principal.is_internal or not await service.write_own_flag(session, body.notify_whatsapp):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return NotifySettingsOut(notify_whatsapp=body.notify_whatsapp, can_notify=True)
