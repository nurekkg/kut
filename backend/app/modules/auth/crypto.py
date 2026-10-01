import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.modules.auth.keys import get_totp_keys

_NONCE_BYTES = 12


class SecretDecryptionError(Exception):
    """Секрет не расшифровывается: неизвестный key_id, чужая строка или повреждённые данные."""


def encrypt_totp_secret(secret: str, *, user_id: uuid.UUID) -> tuple[bytes, str]:
    """AES-256-GCM; ключ и его идентификатор (для ротации) возвращаются вместе с шифртекстом.

    Идентификатор пользователя входит в AAD: шифртекст не перенести в строку другого пользователя.
    """
    keys = get_totp_keys()
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(keys.keys[keys.active_key_id]).encrypt(nonce, secret.encode(), user_id.bytes)
    return nonce + sealed, keys.active_key_id


def decrypt_totp_secret(blob: bytes, key_id: str, *, user_id: uuid.UUID) -> str:
    key = get_totp_keys().keys.get(key_id)
    if key is None or len(blob) <= _NONCE_BYTES:
        raise SecretDecryptionError(f"unknown key id or short blob (key_id={key_id!r})")
    try:
        return AESGCM(key).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], user_id.bytes).decode()
    except InvalidTag as error:
        raise SecretDecryptionError("authentication tag mismatch") from error
