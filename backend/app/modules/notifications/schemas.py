import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DeliveryOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    event_type: str
    template_code: str
    document_id: uuid.UUID | None
    document_number: str | None
    recipient_role: str | None
    recipient_role_name: str | None
    attempt: int
    status: str
    error_code: str | None
    phone_masked: str
    defer_until: datetime | None


class NotifySettingsOut(BaseModel):
    notify_whatsapp: bool
    can_notify: bool
    consent_at: datetime | None = None
    revoked_at: datetime | None = None
    consent_text_version: str | None = None


class NotifySettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notify_whatsapp: bool
    consent_text_version: str | None = None
