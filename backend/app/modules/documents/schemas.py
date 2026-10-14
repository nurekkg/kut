import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.documents.types import MAX_FILE_BYTES

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
# SHA-256 в base64: 32 байта = 44 символа с отступом.
Sha256Base64 = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9+/]{43}=$", min_length=44, max_length=44)
]
ContentType = Annotated[str, StringConstraints(max_length=128)]
LinkKind = Literal["project_node", "work", "work_actual", "equipment_unit"]


class KindOut(BaseModel):
    code: str
    prefix: str
    scope: str
    financial: bool
    counterparty_required: bool
    contract_required: bool
    links: list[str]


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LinkKind
    id: uuid.UUID


class LinkOut(BaseModel):
    kind: str
    id: uuid.UUID


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=32)
    legal_entity_id: uuid.UUID
    project_id: uuid.UUID | None = None
    counterparty_id: uuid.UUID | None = None
    contract_id: uuid.UUID | None = None
    title: Title
    links: list[LinkIn] = Field(default_factory=list, max_length=20)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)


class VersionOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version_no: int
    status: str
    content_type: str
    size_bytes: int
    created_at: datetime
    comment: str | None = None
    checksum_sha256: str | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    kind: str
    financial: bool
    title: str
    project_id: uuid.UUID | None
    counterparty_id: uuid.UUID | None
    created_at: datetime
    # Внешним ролям не отдаются номер, юрлицо, договор, связи и служебные поля
    # (ADR часть 2 раздел 7.5).
    registration_no: str | None = None
    legal_entity_id: uuid.UUID | None = None
    contract_id: uuid.UUID | None = None
    archived_at: datetime | None = None
    current_version: VersionOut | None = None
    amount: Decimal | None = None
    links: list[LinkOut] = Field(default_factory=list)


class DocumentDetail(DocumentOut):
    versions: list[VersionOut] = Field(default_factory=list)


class PresignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: ContentType
    size_bytes: int = Field(gt=0, le=MAX_FILE_BYTES)
    checksum_sha256: Sha256Base64


class PresignOut(BaseModel):
    key: str
    upload_url: str
    headers: dict[str, str]
    expires_in: int


class VersionConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=512)
    comment: Comment | None = None


class DownloadOut(BaseModel):
    url: str
    expires_in: int


class AmountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
