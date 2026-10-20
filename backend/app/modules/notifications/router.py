from datetime import datetime
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
    SELECT d.id, d.created_at, o.event_type, o.template_code, o.document_id,
           NULLIF(o.params->>'number', '') AS document_number,
           r.code AS recipient_role, r.name AS recipient_role_name,
           d.attempt, d.status, d.error_code, d.phone_masked, d.defer_until
    FROM notification_delivery d
    JOIN notification_outbox o ON o.id = d.outbox_id
    LEFT JOIN "user" u ON u.id = o.recipient_user_id
    LEFT JOIN role r ON r.id = u.role_id
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


def _settings_out(
    flag: bool, consent_at: datetime | None, revoked_at: datetime | None
) -> NotifySettingsOut:
    return NotifySettingsOut(
        notify_whatsapp=flag, can_notify=True, consent_at=consent_at, revoked_at=revoked_at
    )


@router.get("/settings", response_model=NotifySettingsOut)
async def read_settings(session: Db, principal: Me) -> NotifySettingsOut:
    # Портал не читает таблицу user: у его логина нет доступа к колонке, и уведомлений ему нет.
    if not principal.is_internal:
        return NotifySettingsOut(notify_whatsapp=False, can_notify=False)
    return _settings_out(*await service.read_own_settings(session))


@router.patch("/settings", response_model=NotifySettingsOut)
async def update_settings(body: NotifySettingsIn, session: Db, principal: Me) -> NotifySettingsOut:
    if not principal.is_internal or not await service.write_own_flag(session, body.notify_whatsapp):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return _settings_out(*await service.read_own_settings(session))
