import uuid
from datetime import datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase

# Допустимые типы и расширения ключей; предел размера - решение PO (15 МБ), он же в CHECK таблицы.
EXTENSIONS: Final[dict[str, str]] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/webp": "webp",
}
MAX_PHOTO_BYTES: Final = 15 * 1024 * 1024

_CONTENT_TYPES = ", ".join(f"'{kind}'" for kind in EXTENSIONS)


class WorkPhoto(BusinessBase):
    """Метаданные фото факта (ТЗ §5.3, §5.14): файл лежит в S3, в БД - ключ, тип, размер, геометка.

    Ключ начинается с project/<id объекта>/ (CHECK), запись неизменяема, кроме мягкого удаления.
    Внешним ролям фото не отдаются (в MVP их видят только внутренние).
    """

    __tablename__ = "work_photo"

    project_id: Mapped[uuid.UUID]
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_actual_id: Mapped[uuid.UUID]
    s3_key: Mapped[str]
    content_type: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    taken_at: Mapped[datetime | None] = mapped_column(default=None)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_work_photo_project_id_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "work_actual_id"],
            ["work_actual.project_id", "work_actual.id"],
            name="fk_work_photo_project_id_work_actual",
        ),
        UniqueConstraint("s3_key", name="uq_work_photo_s3_key"),
        CheckConstraint(f"content_type IN ({_CONTENT_TYPES})", name="content_type_valid"),
        CheckConstraint(f"size_bytes > 0 AND size_bytes <= {MAX_PHOTO_BYTES}", name="size_valid"),
        CheckConstraint(
            "starts_with(s3_key, 'project/' || project_id::text || '/')", name="key_in_project"
        ),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)"
            " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90"
            " AND longitude BETWEEN -180 AND 180))",
            name="coordinates_valid",
        ),
        Index("ix_work_photo_project_id_work_actual_id", "project_id", "work_actual_id"),
    )
