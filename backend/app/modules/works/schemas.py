import uuid
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkCreate(_Strict):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=300)
    unit: str = Field(min_length=1, max_length=30)
    category: str = Field(min_length=1, max_length=100)


class WorkPatch(_Strict):
    """Меняются только присланные поля; ни одно из них не может быть null."""

    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    unit: str | None = Field(default=None, min_length=1, max_length=30)
    category: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class WorkOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    unit: str
    category: str
