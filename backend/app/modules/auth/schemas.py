import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.auth.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(_Strict):
    identifier: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("identifier")
    @classmethod
    def _no_control_characters(cls, value: str) -> str:
        # NUL в строке не принимает ни одно текстовое поле PostgreSQL (был бы ответ 500).
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("control characters are not allowed")
        return value


class MfaVerifyRequest(_Strict):
    mfa_token: str = Field(min_length=10, max_length=4096)
    code: str | None = Field(default=None, max_length=16)
    recovery_code: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _exactly_one(self) -> "MfaVerifyRequest":
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("provide either code or recovery_code")
        return self


class MfaConfirmRequest(_Strict):
    code: str = Field(min_length=6, max_length=16)
    mfa_token: str | None = Field(default=None, max_length=4096)


class MfaSetupRequest(_Strict):
    mfa_token: str | None = Field(default=None, max_length=4096)


class PasswordChangeRequest(_Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class MeResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    role: str
    scope: str
    is_internal: bool
    counterparty_id: uuid.UUID | None
    permissions: list[str]
    project_ids: list[uuid.UUID]
    amr: list[str]


class TokenResponse(BaseModel):
    status: Literal["ok"] = "ok"
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - тип схемы авторизации, не секрет
    expires_in: int
    user: MeResponse


class MfaRequiredResponse(BaseModel):
    status: Literal["mfa_required", "mfa_enrollment_required"]
    mfa_token: str
    expires_in: int


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaConfirmResponse(BaseModel):
    recovery_codes: list[str]
    session: TokenResponse | None = None
