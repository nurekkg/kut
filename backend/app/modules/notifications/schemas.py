import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DeliveryOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    event_type: str
    template_code: str
    attempt: int
    status: str
    error_code: str | None
    phone_masked: str


class NotifySettingsOut(BaseModel):
    notify_whatsapp: bool
    can_notify: bool


class NotifySettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notify_whatsapp: bool
