import uuid
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestIdMiddleware:
    """request_id запроса: принимаем валидный UUID из X-Request-ID, иначе генерируем свой.

    Он попадает в RequestContext (GUC app.request_id) и в журнал аудита, а клиенту возвращается
    заголовком. Чистый ASGI-middleware, чтобы не ломать передачу ContextVar в обработчики.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _parse(dict(scope["headers"]).get(b"x-request-id"))
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", str(request_id).encode()),
                ]
            await send(message)

        await self.app(scope, receive, send_with_header)


def _parse(raw: bytes | None) -> uuid.UUID:
    if raw:
        try:
            return uuid.UUID(raw.decode())
        except (ValueError, UnicodeDecodeError):
            pass
    return uuid.uuid4()


def get_request_id(request: Request) -> uuid.UUID:
    """request_id текущего запроса; вне RequestIdMiddleware (например, в тестах) создаётся новый."""
    value = getattr(request.state, "request_id", None)
    if not isinstance(value, uuid.UUID):
        value = uuid.uuid4()
        request.state.request_id = value
    return value
