import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.org.models import CounterpartyType

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_JUNK = re.compile(r"[\s\-().]")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _blank_to_none(value: str | None) -> str | None:
    return value or None


class LegalEntityIn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    tax_id: str | None = Field(default=None, max_length=50)

    _tax = field_validator("tax_id")(_blank_to_none)


class LegalEntityOut(BaseModel):
    id: uuid.UUID
    name: str
    tax_id: str | None
    project_count: int


class CounterpartyIn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    tax_id: str | None = Field(default=None, max_length=50)
    types: list[CounterpartyType] = Field(min_length=1)

    _tax = field_validator("tax_id")(_blank_to_none)

    @field_validator("types")
    @classmethod
    def _unique(cls, value: list[CounterpartyType]) -> list[CounterpartyType]:
        return list(dict.fromkeys(value))


class CounterpartyOut(BaseModel):
    id: uuid.UUID
    name: str
    tax_id: str | None
    types: list[str]


class RoleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    scope: str
    requires_2fa: bool


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    legal_entity_id: uuid.UUID


class UserCreate(_Strict):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    role_id: uuid.UUID
    counterparty_id: uuid.UUID | None = None

    _email = field_validator("email")(_blank_to_none)
    _phone = field_validator("phone")(_blank_to_none)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.lower()
        if not _EMAIL.match(value):
            raise ValueError("invalid email")
        return value

    @field_validator("phone")
    @classmethod
    def _phone_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = _PHONE_JUNK.sub("", value)
        digits = digits if digits.startswith("+") else f"+{digits}"
        if not re.fullmatch(r"\+[1-9][0-9]{6,14}", digits):
            raise ValueError("invalid phone")
        return digits

    @model_validator(mode="after")
    def _has_a_contact(self) -> "UserCreate":
        if self.email is None and self.phone is None:
            raise ValueError("a phone or an email is required")
        return self


class UserAdminOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    role_id: uuid.UUID
    role: str
    role_name: str
    scope: str
    is_active: bool
    mfa_enabled: bool
    requires_2fa: bool
    pending_activation: bool
    counterparty_id: uuid.UUID | None
    counterparty_name: str | None
    project_ids: list[uuid.UUID]
    legal_entity_ids: list[uuid.UUID]
    notify_whatsapp: bool
    notify_whatsapp_consent_at: datetime | None
    notify_whatsapp_consent_version: str | None = None


class RoleChange(_Strict):
    role_id: uuid.UUID


class ActiveChange(_Strict):
    active: bool


class AccessChange(_Strict):
    project_ids: list[uuid.UUID] = Field(max_length=500)
    legal_entity_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class InviteOut(BaseModel):
    path: str
    expires_at: datetime


class EventOut(BaseModel):
    id: int
    occurred_at: datetime
    action: str
    actor_id: uuid.UUID | None
    actor_name: str | None
    actor_kind: str
    ip: str | None
    subject_id: uuid.UUID | None
    subject_name: str | None
    meta: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventOut]
    next_before: int | None
