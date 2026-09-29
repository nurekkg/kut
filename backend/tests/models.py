from sqlalchemy.orm import DeclarativeBase, Mapped

from app.core.base import (
    AuditUserMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ScratchBase(DeclarativeBase):
    type_annotation_map = Base.type_annotation_map


class Widget(UUIDPrimaryKeyMixin, TimestampMixin, AuditUserMixin, SoftDeleteMixin, ScratchBase):
    __tablename__ = "widget"

    name: Mapped[str]
