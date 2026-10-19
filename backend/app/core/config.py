from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"

    database_url: SecretStr
    database_portal_url: SecretStr | None = None
    redis_url: SecretStr
    celery_broker_url: SecretStr
    celery_result_backend: SecretStr

    s3_endpoint_url: str
    # Адрес S3 в подписанных ссылках для браузера (в docker-сети s3_endpoint_url ему недоступен);
    # не задан - подписи идут с s3_endpoint_url.
    s3_public_endpoint_url: str | None = None
    s3_region: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: SecretStr

    health_check_timeout_seconds: float = 2.0
    # Ответ Redis ждём дольше проверки здоровья: пока цикл событий API занят (большой список ГПР в
    # одном потоке), ожидающий чтения запрос иначе падал с TimeoutError через 2 с и отвечал 500.
    redis_socket_timeout_seconds: float = 10.0

    # Фото факта: ссылка на загрузку живёт 15 минут (медленная мобильная связь), на скачивание - 2.
    photo_upload_ttl_seconds: int = 900
    photo_download_ttl_seconds: int = 120

    # Ответы API короче порога не сжимаются; уровень 5 - компромисс скорости и размера (дерево на
    # 10 тыс. узлов: см. wiki/arch/s3a-schedule-and-actuals.md).
    gzip_minimum_bytes: int = 1024
    gzip_compresslevel: int = 5

    # Дашборды (S4): пороги «проблем» и размеры списков - настройки, а не литералы в расчёте.
    dashboard_problem_lag_days: int = (
        3  # работа проблемна, если отстаёт на столько дней или просрочена
    )
    dashboard_approval_stale_days: int = 2  # факт на подтверждении дольше этого - «зависший»
    dashboard_recent_days: int = 7  # окно «фактов за последние дни»
    dashboard_top_n: int = 10  # проблемные работы и зоны на экране
    dashboard_series_window_weeks: int = 52  # ряд по неделям: окно в обе стороны от сегодня
    dashboard_photos_limit: int = 6  # последние фото в ленте

    # --- аутентификация (ADR-001 §2). Ключи только из окружения; API без них не стартует. ---
    # «kid:<base64url 32-байтного seed Ed25519>[,kid:...]»; подписывает ключ jwt_active_kid,
    # проверяются все перечисленные (так ротируют ключи).
    jwt_private_keys: SecretStr | None = None
    jwt_active_kid: str | None = None
    jwt_issuer: str = "kut"
    jwt_audience: str = "kut-api"
    access_ttl_seconds: int = 600
    mfa_token_ttl_seconds: int = 300
    refresh_ttl_days: int = 14
    refresh_ttl_days_field: int = 30  # прораб и мастер: личные телефоны, ADR-001 §7, вопрос 5
    # Повтор уже использованного refresh в этом окне отдаёт прежнюю пару, за окном семья отзывается.
    refresh_grace_seconds: int = 30
    invite_ttl_hours: int = 72  # срок одноразовой ссылки активации
    # «key_id:<base64url 32 байт>[,key_id:...]» для AES-GCM; шифрует totp_active_key_id.
    totp_encryption_keys: SecretStr | None = None
    totp_active_key_id: str | None = None
    totp_issuer: str = "Kut"
    cookie_secure: bool | None = None  # None: Secure везде, кроме development
    # Доверять последнему X-Forwarded-For. Не задано: да только в development (API за прокси web),
    # в test и production нет; в production включается явно и только за доверенным прокси.
    trust_forwarded_for: bool | None = None
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 900
    principal_cache_ttl_seconds: int = 60

    idempotency_ttl_hours: int = 24
    idempotency_lock_timeout_ms: int = 10000  # сколько параллельный повтор ждёт первый запрос

    maintenance_purge_seconds: int = 3600
    maintenance_partitions_seconds: int = 86400
    audit_partitions_ahead: int = 24
    # Уведомления согласования (S6b): реальная отправка в WhatsApp выключена по умолчанию.
    whatsapp_enabled: bool = False
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_api_version: str = "v21.0"
    app_public_url: str

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "production"

    @property
    def forwarded_for_trusted(self) -> bool:
        if self.trust_forwarded_for is not None:
            return self.trust_forwarded_for
        return self.app_env == "development"

    @property
    def cookies_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env != "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
