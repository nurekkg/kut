from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings

WHATSAPP_BASE_URL = "https://graph.facebook.com"
HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class SendResult:
    ok: bool
    retryable: bool
    ok_status: str
    provider_status: int | None
    message_id: str | None
    error_code: str | None


class NotificationAdapter(Protocol):
    requires_approved_template: bool

    def send(self, *, to: str, template: str, language: str, params: list[str]) -> SendResult: ...


def mask_phone(phone: str | None) -> str:
    """Маска для журнала: код страны и последние две цифры, остальное скрыто."""
    if not phone:
        return ""
    digits = phone.lstrip("+")
    return f"+{digits[:3]} *** *** {digits[-2:]}"


class LogNotificationAdapter:
    """Dev и тесты: сети нет, доставка записывается со статусом logged."""

    requires_approved_template = False

    def send(self, *, to: str, template: str, language: str, params: list[str]) -> SendResult:
        return SendResult(
            ok=True,
            retryable=False,
            ok_status="logged",
            provider_status=None,
            message_id=None,
            error_code=None,
        )


class WhatsAppCloudAdapter:
    """Шаблонное сообщение WhatsApp Cloud API. Токен - только в заголовке, не в логах."""

    requires_approved_template = True

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        api_version: str,
        transport: httpx.BaseTransport | None = None,
        base_url: str = WHATSAPP_BASE_URL,
    ) -> None:
        self._url = f"{base_url}/{api_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._client = httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, transport=transport)

    def send(self, *, to: str, template: str, language: str, params: list[str]) -> SendResult:
        body = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": item} for item in params],
                    }
                ],
            },
        }
        try:
            response = self._client.post(self._url, json=body, headers=self._headers)
        except httpx.TimeoutException:
            return _failure("timeout", None, retryable=True)
        except httpx.HTTPError:
            return _failure("network", None, retryable=True)
        code = response.status_code
        if code == 200:
            try:
                message_id = response.json()["messages"][0]["id"]
            except (ValueError, KeyError, IndexError, TypeError):
                return _failure("bad_response", code, retryable=False)
            return SendResult(
                ok=True,
                retryable=False,
                ok_status="sent",
                provider_status=code,
                message_id=message_id,
                error_code=None,
            )
        retryable = code == 429 or code >= 500
        return _failure(f"http_{code}", code, retryable=retryable)


def _failure(error_code: str, provider_status: int | None, *, retryable: bool) -> SendResult:
    return SendResult(
        ok=False,
        retryable=retryable,
        ok_status="",
        provider_status=provider_status,
        message_id=None,
        error_code=error_code,
    )


def get_adapter(settings: Settings) -> NotificationAdapter:
    if not settings.whatsapp_enabled:
        return LogNotificationAdapter()
    assert settings.whatsapp_phone_number_id is not None
    assert settings.whatsapp_access_token is not None
    return WhatsAppCloudAdapter(
        phone_number_id=settings.whatsapp_phone_number_id,
        access_token=settings.whatsapp_access_token.get_secret_value(),
        api_version=settings.whatsapp_api_version,
    )


def validate_notification_config(settings: Settings) -> None:
    """Включённый WhatsApp без номера или токена - ошибка при старте, а не тихий отказ при отправке."""
    if not settings.whatsapp_enabled:
        return
    if not settings.whatsapp_phone_number_id or settings.whatsapp_access_token is None:
        raise RuntimeError(
            "WHATSAPP_ENABLED=true требует WHATSAPP_PHONE_NUMBER_ID и WHATSAPP_ACCESS_TOKEN"
        )
