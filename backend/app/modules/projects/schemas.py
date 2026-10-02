import math
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.objects.models import LifecycleStage, NodeType, ProjectStatus

MAX_COORDINATE = 1_000_000.0


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)


def _blank_to_none(value: str | None) -> str | None:
    return value or None


Point = Annotated[tuple[float, float], Field(description="[x, y] в локальных координатах схемы")]


def _check_polygon(value: list[Point] | None) -> list[Point] | None:
    if value is None:
        return None
    for x, y in value:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("polygon coordinates must be finite")
        if abs(x) > MAX_COORDINATE or abs(y) > MAX_COORDINATE:
            raise ValueError("polygon coordinates are out of range")
    return value


Polygon = Annotated[list[Point], Field(min_length=3, max_length=1000)]


# --- объекты ----------------------------------------------------------------------------------


class ProjectFields(_Strict):
    name: str = Field(min_length=1, max_length=200)
    status: ProjectStatus = ProjectStatus.ACTIVE
    lifecycle_stage: LifecycleStage = LifecycleStage.LAND_PURCHASE
    planned_start: date | None = None
    planned_end: date | None = None
    planned_budget: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    customer_id: uuid.UUID | None = None
    address: str | None = Field(default=None, max_length=500)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    warranty_until: date | None = None

    _address = field_validator("address")(_blank_to_none)


class ProjectCreate(ProjectFields):
    legal_entity_id: uuid.UUID

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_dates(self.planned_start, self.planned_end)
        _check_coordinates(self.latitude, self.longitude)
        return self


class ProjectPatch(_Strict):
    """Меняются только присланные поля; null очищает необязательное. Юрлицо объекта не меняется."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: ProjectStatus | None = None
    lifecycle_stage: LifecycleStage | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    planned_budget: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    customer_id: uuid.UUID | None = None
    address: str | None = Field(default=None, max_length=500)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    warranty_until: date | None = None

    _address = field_validator("address")(_blank_to_none)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for required in ("name", "status", "lifecycle_stage"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        if {"planned_start", "planned_end"} <= self.model_fields_set:
            _check_dates(self.planned_start, self.planned_end)
        if {"latitude", "longitude"} <= self.model_fields_set:
            _check_coordinates(self.latitude, self.longitude)
        elif {"latitude", "longitude"} & self.model_fields_set:
            raise ValueError("latitude and longitude are set together")
        return self


def _check_dates(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("planned_end is before planned_start")


def _check_coordinates(latitude: Decimal | None, longitude: Decimal | None) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude are set together")


# Поля карточки, которые лежат в project_finance и видны только ролям с правом finance:read.
FINANCE_FIELDS = (
    "planned_budget",
    "customer_id",
    "address",
    "latitude",
    "longitude",
    "warranty_until",
)


class ProjectOut(BaseModel):
    """Поля после id, name, status и legal_entity_id внешним ролям не отдаются (None): их колонки
    закрыты для пула kut_portal. Бюджет, заказчик, адрес, координаты и гарантия отдаются только
    ролям с правом finance:read (finance_visible): остальным они None, и это граница БД (RLS
    таблицы project_finance), а не только условие в ответе."""

    id: uuid.UUID
    name: str
    status: str
    legal_entity_id: uuid.UUID
    lifecycle_stage: str | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    planned_budget: Decimal | None = None
    budget_currency: str | None = None
    customer_id: uuid.UUID | None = None
    customer_name: str | None = None
    address: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    warranty_until: date | None = None
    node_count: int | None = None
    finance_visible: bool = False


# --- узлы дерева ------------------------------------------------------------------------------


class NodeFields(_Strict):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    planned_start: date | None = None
    planned_end: date | None = None
    foreman_user_id: uuid.UUID | None = None
    master_user_id: uuid.UUID | None = None
    polygon: Polygon | None = None

    _code = field_validator("code")(_blank_to_none)
    _polygon = field_validator("polygon")(_check_polygon)


class NodeCreate(NodeFields):
    parent_id: uuid.UUID | None = None
    type: NodeType
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_dates(self.planned_start, self.planned_end)
        return self


class NodePatch(_Strict):
    """Меняются только присланные поля; null очищает необязательное. Перенос - отдельный запрос."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    type: NodeType | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    foreman_user_id: uuid.UUID | None = None
    master_user_id: uuid.UUID | None = None
    polygon: Polygon | None = None

    _code = field_validator("code")(_blank_to_none)
    _polygon = field_validator("polygon")(_check_polygon)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for required in ("name", "type"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        if {"planned_start", "planned_end"} <= self.model_fields_set:
            _check_dates(self.planned_start, self.planned_end)
        return self


class NodeMove(_Strict):
    """Новый родитель (null - корень объекта) и позиция среди его детей (по умолчанию в конец)."""

    parent_id: uuid.UUID | None = None
    index: int | None = Field(default=None, ge=0, le=100_000)


class NodeOut(BaseModel):
    """Плоская запись дерева: родитель и путь (id предков от корня до самого узла).

    Ответственные (foreman, master) внешним ролям не отдаются: их колонки закрыты для kut_portal.
    """

    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    type: str
    name: str
    code: str | None
    sort_order: int
    path: list[uuid.UUID]
    planned_start: date | None
    planned_end: date | None
    polygon: list[list[float]] | None
    foreman_user_id: uuid.UUID | None = None
    master_user_id: uuid.UUID | None = None


class NodeDetail(NodeOut):
    foreman_name: str | None = None
    master_name: str | None = None
