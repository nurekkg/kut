"""Ответы большими списками без двойной работы FastAPI.

Для `response_model=list[Модель]` FastAPI на каждый элемент делает model_dump, затем проверяет
результат заново и только потом пишет JSON, и всё это в потоке цикла событий. На ГПР в 4 800 строк
выходит несколько секунд, пока API никому не отвечает: ответы Redis и проверка здоровья (по 2
секунды) не успевают, и параллельные запросы получают 500. Здесь список пишется один раз, сразу
в байты (pydantic-core); JSON тот же, `response_model` остаётся в описании API.
"""

from collections.abc import Sequence
from functools import lru_cache

from fastapi import Response
from pydantic import BaseModel, TypeAdapter


@lru_cache
def _adapter(model: type[BaseModel]) -> TypeAdapter[list[BaseModel]]:
    return TypeAdapter(list[model])  # type: ignore[valid-type]


def json_list(items: Sequence[BaseModel], model: type[BaseModel]) -> Response:
    return Response(content=_adapter(model).dump_json(list(items)), media_type="application/json")
