import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, BeforeValidator, Field, computed_field, model_validator

from app.core.schemas import StrictModel

SIX_PLACES = Decimal("0.000001")
TWO_PLACES = Decimal("0.01")


def _quantize(places: Decimal) -> BeforeValidator:
    """Координаты и точность приходят из геолокации браузера с лишними знаками: округляем."""

    def convert(value: Any) -> Any:
        if isinstance(value, int | float | str) and not isinstance(value, bool):
            try:
                return Decimal(str(value)).quantize(places)
            except ArithmeticError:
                return value
        return value

    return BeforeValidator(convert)


Volume = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]
Latitude = Annotated[
    Decimal, _quantize(SIX_PLACES), Field(ge=-90, le=90, max_digits=9, decimal_places=6)
]
Longitude = Annotated[
    Decimal, _quantize(SIX_PLACES), Field(ge=-180, le=180, max_digits=9, decimal_places=6)
]
Accuracy = Annotated[
    Decimal, _quantize(TWO_PLACES), Field(ge=0, le=100_000, max_digits=8, decimal_places=2)
]


def _check_geo(
    latitude: Decimal | None, longitude: Decimal | None, accuracy: Decimal | None
) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude are set together")
    if accuracy is not None and latitude is None:
        raise ValueError("accuracy needs the coordinates")


class ActualCreate(StrictModel):
    """submit=true: факт сразу уходит на проверку (мастер вносит и отправляет); факт прораба и
    техдиректора БД согласует сразу в любом случае."""

    node_id: uuid.UUID
    work_id: uuid.UUID
    schedule_item_id: uuid.UUID | None = None
    volume: Volume
    work_date: date
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    accuracy_m: Accuracy | None = None
    submit: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_geo(self.latitude, self.longitude, self.accuracy_m)
        return self


class ActualPatch(StrictModel):
    """Меняются только присланные поля; null очищает привязку к ГПР и геометку. Статус меняют
    отдельные действия (submit, approve, reject), а не правка."""

    node_id: uuid.UUID | None = None
    work_id: uuid.UUID | None = None
    schedule_item_id: uuid.UUID | None = None
    volume: Volume | None = None
    work_date: date | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    accuracy_m: Accuracy | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for required in ("node_id", "work_id", "volume", "work_date"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        if {"latitude", "longitude"} & self.model_fields_set and not {
            "latitude",
            "longitude",
        } <= self.model_fields_set:
            raise ValueError("latitude and longitude are set together")
        return self


class ActualReject(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)


class ActualOut(BaseModel):
    """Внешним ролям отдаётся только подтверждённый факт и без людей: автор, подтвердивший, причина
    отклонения, геометка и число фото закрыты для пула kut_portal (None)."""

    id: uuid.UUID
    project_id: uuid.UUID
    node_id: uuid.UUID
    work_id: uuid.UUID
    work_code: str | None = None
    work_name: str | None = None
    unit: str | None = None
    schedule_item_id: uuid.UUID | None
    volume: Decimal
    work_date: date
    status: str
    approved_at: datetime | None
    created_at: datetime
    author_id: uuid.UUID | None = None
    author_name: str | None = None
    approved_by: uuid.UUID | None = None
    approved_by_name: str | None = None
    reject_reason: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    accuracy_m: Decimal | None = None
    photo_count: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unplanned(self) -> bool:
        return self.schedule_item_id is None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warnings(self) -> list[str]:
        """ТЗ §5.3: факт на узел без плана в ГПР разрешён, но с предупреждением."""
        return ["unplanned"] if self.unplanned else []
