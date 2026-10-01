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
    s3_region: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: SecretStr

    health_check_timeout_seconds: float = 2.0

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
    # «key_id:<base64url 32 байт>[,key_id:...]» для AES-GCM; шифрует totp_active_key_id.
    totp_encryption_keys: SecretStr | None = None
    totp_active_key_id: str | None = None
    totp_issuer: str = "Kut"
    cookie_secure: bool | None = None  # None: Secure везде, кроме development
    trust_forwarded_for: bool = False  # доверять последнему X-Forwarded-For (за прокси web)
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 900
    principal_cache_ttl_seconds: int = 60

    idempotency_ttl_hours: int = 24
    idempotency_lock_timeout_ms: int = 10000  # сколько параллельный повтор ждёт первый запрос

    maintenance_purge_seconds: int = 3600
    maintenance_partitions_seconds: int = 86400
    audit_partitions_ahead: int = 24

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "production"

    @property
    def cookies_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env != "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
