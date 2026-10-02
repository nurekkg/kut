from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Тело запроса: лишние поля - ошибка, строки без краевых пробелов, enum как значения."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)
