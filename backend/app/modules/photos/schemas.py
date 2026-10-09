import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.core.schemas import StrictModel
from app.modules.actuals.schemas import Latitude, Longitude
from app.modules.photos.models import MAX_PHOTO_BYTES

ContentType = Literal["image/jpeg", "image/png", "image/heic", "image/webp"]
MAX_FILES_PER_REQUEST = 10


class PhotoFile(StrictModel):
    content_type: ContentType
    size_bytes: Annotated[int, Field(gt=0, le=MAX_PHOTO_BYTES)]


class PresignRequest(StrictModel):
    """Ссылки на загрузку для нескольких фото одного факта: файлы идут прямо в S3, мимо API."""

    work_actual_id: uuid.UUID
    files: Annotated[list[PhotoFile], Field(min_length=1, max_length=MAX_FILES_PER_REQUEST)]


class PresignedUpload(BaseModel):
    """Загрузка: PUT на upload_url с этими заголовками и телом ровно size_bytes байт."""

    key: str
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_in: int


class PresignResponse(BaseModel):
    items: list[PresignedUpload]


class PhotoConfirmItem(StrictModel):
    key: Annotated[str, Field(min_length=1, max_length=300)]
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    taken_at: datetime | None = None


class PhotoConfirm(StrictModel):
    """Подтверждение загрузки: сервер сам проверяет объект в S3 (HEAD), а не верит клиенту."""

    work_actual_id: uuid.UUID
    items: Annotated[list[PhotoConfirmItem], Field(min_length=1, max_length=MAX_FILES_PER_REQUEST)]


class PhotoOut(BaseModel):
    id: uuid.UUID
    work_actual_id: uuid.UUID
    content_type: str
    size_bytes: int
    latitude: Decimal | None
    longitude: Decimal | None
    taken_at: datetime | None
    uploaded_by: uuid.UUID
    created_at: datetime
    url: str | None = None
    url_expires_in: int | None = None


class DownloadUrl(BaseModel):
    url: str
    expires_in: int
