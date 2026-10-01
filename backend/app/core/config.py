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

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
