import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import jwt

from app.core.config import get_settings
from app.modules.auth.keys import get_jwt_keys

ACCESS = "access"
MFA = "mfa"
MfaScope = Literal["verify", "enroll"]

_REQUIRED = ["exp", "iat", "sub", "jti", "typ"]


class InvalidTokenError(Exception):
    """Подпись, срок, тип или обязательные claims токена не прошли проверку."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    sid: uuid.UUID
    role_ver: int
    amr: tuple[str, ...]
    otp_at: int | None
    expires_at: int


@dataclass(frozen=True, slots=True)
class MfaClaims:
    user_id: uuid.UUID
    scope: MfaScope
    expires_at: int


def _encode(claims: dict[str, Any]) -> str:
    settings = get_settings()
    keys = get_jwt_keys()
    now = int(time.time())
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "jti": uuid.uuid4().hex,
        **claims,
    }
    return jwt.encode(
        payload,
        keys.private[keys.active_kid],
        algorithm="EdDSA",
        headers={"kid": keys.active_kid},
    )


def _decode(token: str, *, typ: str) -> dict[str, Any]:
    settings = get_settings()
    keys = get_jwt_keys()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        public = keys.public.get(kid) if isinstance(kid, str) else None
        if public is None:
            raise InvalidTokenError("unknown key id")
        claims: dict[str, Any] = jwt.decode(
            token,
            public,
            algorithms=["EdDSA"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": _REQUIRED},
        )
    except jwt.PyJWTError as error:
        raise InvalidTokenError(str(error)) from error
    if claims.get("typ") != typ:
        raise InvalidTokenError("wrong token type")
    return claims


def issue_access(
    *,
    user_id: uuid.UUID,
    sid: uuid.UUID,
    role_ver: int,
    amr: tuple[str, ...],
    otp_at: int | None = None,
) -> tuple[str, int]:
    """Возвращает (JWT, срок жизни в секундах). Прав в токене нет: они берутся по role_ver."""
    ttl = get_settings().access_ttl_seconds
    claims: dict[str, Any] = {
        "typ": ACCESS,
        "sub": str(user_id),
        "sid": str(sid),
        "role_ver": role_ver,
        "amr": list(amr),
        "exp": int(time.time()) + ttl,
    }
    if otp_at is not None:
        claims["otp_at"] = otp_at
    return _encode(claims), ttl


def issue_mfa(*, user_id: uuid.UUID, scope: MfaScope) -> tuple[str, int]:
    ttl = get_settings().mfa_token_ttl_seconds
    claims = {
        "typ": MFA,
        "sub": str(user_id),
        "scope": scope,
        "amr": ["pwd"],
        "exp": int(time.time()) + ttl,
    }
    return _encode(claims), ttl


def decode_access(token: str) -> AccessClaims:
    claims = _decode(token, typ=ACCESS)
    try:
        return AccessClaims(
            user_id=uuid.UUID(claims["sub"]),
            sid=uuid.UUID(claims["sid"]),
            role_ver=int(claims["role_ver"]),
            amr=tuple(str(item) for item in claims.get("amr", [])),
            otp_at=int(claims["otp_at"]) if claims.get("otp_at") is not None else None,
            expires_at=int(claims["exp"]),
        )
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidTokenError("malformed claims") from error


def decode_mfa(token: str, *, scope: MfaScope | None = None) -> MfaClaims:
    claims = _decode(token, typ=MFA)
    found = claims.get("scope")
    if found not in ("verify", "enroll") or (scope is not None and found != scope):
        raise InvalidTokenError("wrong mfa scope")
    try:
        return MfaClaims(
            user_id=uuid.UUID(claims["sub"]), scope=found, expires_at=int(claims["exp"])
        )
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidTokenError("malformed claims") from error
