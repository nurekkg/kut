import base64
import binascii
from dataclasses import dataclass
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.core.config import get_settings

# Ключи, помеченные так, — примеры из .env.example: годятся только для локальной разработки.
DEV_KEY_PREFIX = "dev"


class AuthConfigError(RuntimeError):
    """Ключи аутентификации не заданы или некорректны: API не должен стартовать."""


def _decode(value: str, *, what: str, length: int) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as error:
        raise AuthConfigError(f"{what} is not valid base64") from error
    if len(raw) != length:
        raise AuthConfigError(f"{what} must be {length} bytes, got {len(raw)}")
    return raw


def _key_map(value: str | None, *, name: str) -> dict[str, str]:
    """Разбор «kid:ключ[,kid:ключ]»: ключи в base64url не содержат ни «:», ни «,»."""
    if not value or not value.strip():
        raise AuthConfigError(f"{name} is not set")
    result: dict[str, str] = {}
    for item in value.split(","):
        key_id, separator, material = item.strip().partition(":")
        if not separator or not key_id or not material:
            raise AuthConfigError(f"{name} must look like 'kid:key[,kid:key]'")
        if key_id in result:
            raise AuthConfigError(f"{name} lists key id {key_id!r} twice")
        result[key_id] = material
    return result


@dataclass(frozen=True, slots=True)
class JwtKeys:
    active_kid: str
    private: dict[str, Ed25519PrivateKey]
    public: dict[str, Ed25519PublicKey]


@dataclass(frozen=True, slots=True)
class TotpKeys:
    active_key_id: str
    keys: dict[str, bytes]


@lru_cache
def get_jwt_keys() -> JwtKeys:
    settings = get_settings()
    secret = settings.jwt_private_keys
    raw = _key_map(secret.get_secret_value() if secret else None, name="JWT_PRIVATE_KEYS")
    private = {
        kid: Ed25519PrivateKey.from_private_bytes(_decode(seed, what=f"JWT key {kid!r}", length=32))
        for kid, seed in raw.items()
    }
    active = settings.jwt_active_kid
    if not active or active not in private:
        raise AuthConfigError("JWT_ACTIVE_KID must name one of JWT_PRIVATE_KEYS")
    return JwtKeys(
        active_kid=active,
        private=private,
        public={kid: key.public_key() for kid, key in private.items()},
    )


@lru_cache
def get_totp_keys() -> TotpKeys:
    settings = get_settings()
    secret = settings.totp_encryption_keys
    raw = _key_map(secret.get_secret_value() if secret else None, name="TOTP_ENCRYPTION_KEYS")
    keys = {
        key_id: _decode(value, what=f"TOTP key {key_id!r}", length=32)
        for key_id, value in raw.items()
    }
    active = settings.totp_active_key_id
    if not active or active not in keys:
        raise AuthConfigError("TOTP_ACTIVE_KEY_ID must name one of TOTP_ENCRYPTION_KEYS")
    return TotpKeys(active_key_id=active, keys=keys)


def validate_auth_config() -> None:
    """Вызывается при старте API: падаем сразу, а не на первом логине."""
    jwt_keys = get_jwt_keys()
    totp_keys = get_totp_keys()
    if get_settings().app_env == "production":
        placeholders = [
            name for name in (*jwt_keys.private, *totp_keys.keys) if name.startswith(DEV_KEY_PREFIX)
        ]
        if placeholders:
            raise AuthConfigError(
                f"development example keys are not allowed with APP_ENV=production: {placeholders}"
            )
