"""Ответы большими списками и ожидание ответа Redis при занятом цикле событий API."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeout

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.responses import json_list


class Row(BaseModel):
    id: str
    plan_start: date
    volume: Decimal
    note: str | None = None


def test_a_list_response_is_the_same_json_fastapi_would_write() -> None:
    items = [Row(id=str(uuid4()), plan_start=date(2026, 9, 1), volume=Decimal("12.500"))]
    items.append(Row(id=str(uuid4()), plan_start=date(2026, 9, 2), volume=Decimal("0"), note="x"))

    response = json_list(items, Row)

    body = json.loads(bytes(response.body))
    assert response.media_type == "application/json"
    assert body == jsonable_encoder(items)
    assert body[0]["volume"] == "12.500"  # Decimal - строкой, как раньше


def test_an_empty_list_is_an_empty_array() -> None:
    assert json_list([], Row).body == b"[]"


@pytest.fixture
async def slow_redis() -> AsyncIterator[tuple[str, float]]:
    """Сервер Redis, который на PING отвечает через 3 с (цикл событий API был занят)."""
    delay = 3.0

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(1024):
                if b"PING" in data:
                    await asyncio.sleep(delay)
                    writer.write(b"+PONG\r\n")
                else:  # служебные команды при подключении (CLIENT SETINFO) - сразу
                    writer.write(b"+OK\r\n" * data.count(b"*"))
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"redis://127.0.0.1:{port}/0", delay
    server.close()
    await server.wait_closed()


async def test_redis_waits_longer_than_the_health_check_does(
    slow_redis: tuple[str, float],
) -> None:
    url, delay = slow_redis
    settings = get_settings()
    assert settings.redis_socket_timeout_seconds > delay > settings.health_check_timeout_seconds

    client = Redis.from_url(
        url, socket_connect_timeout=2, socket_timeout=settings.redis_socket_timeout_seconds
    )
    try:
        assert await client.ping()  # 3 с ожидания не ошибка
    finally:
        await client.aclose()

    short = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        with pytest.raises(RedisTimeout):  # прежний предел в 2 с на таком ответе падал
            await short.ping()
    finally:
        await short.aclose()


def test_the_shared_client_uses_the_configured_timeout() -> None:
    kwargs = get_redis().connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == get_settings().redis_socket_timeout_seconds
