import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.auth import denylist, limits, repository, totp
from app.modules.auth.crypto import SecretDecryptionError, decrypt_totp_secret, encrypt_totp_secret
from app.modules.auth.errors import (
    InvalidCode,
    InvalidCredentials,
    InvalidCurrentPassword,
    InvalidToken,
    MfaAlreadyEnabled,
    MfaNotStarted,
    PasswordUnchanged,
    RateLimited,
)
from app.modules.auth.principal import Principal, principal_store
from app.modules.auth.repository import ContextRecord
from app.modules.auth.schemas import MeResponse
from app.modules.auth.security import (
    hash_password_async,
    new_opaque_token,
    normalize_login,
    sha256,
    verify_password_async,
)
from app.modules.auth.tokens import (
    InvalidTokenError,
    decode_mfa,
    issue_access,
    issue_mfa,
)

# Прораб и мастер входят с личных телефонов: сессия живёт дольше (ADR-001 §7, вопрос 5).
FIELD_ROLES = frozenset({"foreman", "master"})

MfaOutcome = Literal["mfa_required", "mfa_enrollment_required"]


@dataclass(frozen=True, slots=True)
class IssuedSession:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_max_age: int
    me: MeResponse


@dataclass(frozen=True, slots=True)
class MfaChallenge:
    status: MfaOutcome
    mfa_token: str
    expires_in: int


def me_response(record: ContextRecord | Principal, *, amr: tuple[str, ...]) -> MeResponse:
    return MeResponse(
        id=record.user_id,
        full_name=record.full_name,
        role=record.role_code,
        scope=record.role_scope,
        is_internal=record.role_scope != "counterparty",
        counterparty_id=record.counterparty_id,
        permissions=sorted(record.permissions),
        project_ids=sorted(record.project_ids),
        amr=list(amr),
    )


async def _start_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    amr: tuple[str, ...],
    otp_at: int | None,
    ip: str | None,
) -> IssuedSession:
    record = await principal_store.reload(user_id)
    if record is None:
        raise InvalidCredentials
    settings = get_settings()
    days = (
        settings.refresh_ttl_days_field
        if record.role_code in FIELD_ROLES
        else settings.refresh_ttl_days
    )
    family_id = uuid.uuid4()  # = sid сессии: по нему отзываются и refresh, и access
    refresh_token = new_opaque_token()
    await repository.refresh_issue(
        session,
        user_id=user_id,
        family_id=family_id,
        token_hash=sha256(refresh_token),
        ttl=timedelta(days=days),
        amr=amr,
    )
    await repository.audit_event(
        session,
        "login_success",
        table="user",
        row_id=user_id,
        actor=user_id,
        ip=ip,
        meta={"amr": list(amr)},
    )
    await session.commit()
    access_token, expires_in = issue_access(
        user_id=user_id, sid=family_id, role_ver=record.role_ver, amr=amr, otp_at=otp_at
    )
    return IssuedSession(
        access_token=access_token,
        expires_in=expires_in,
        refresh_token=refresh_token,
        refresh_max_age=days * 86400,
        me=me_response(record, amr=amr),
    )


async def login(
    session: AsyncSession, *, identifier: str, password: str, ip: str
) -> IssuedSession | MfaChallenge:
    login_id = normalize_login(identifier)
    keys = [limits.login_key(login_id), limits.ip_key(ip)]
    if wait := await limits.blocked_for(keys):
        raise RateLimited(wait)

    record = await repository.login_lookup(session, login_id)
    matched = await verify_password_async(record.password_hash if record else None, password)
    if record is None or not record.is_active or not matched:
        await limits.record_failure(keys)
        await repository.audit_event(
            session,
            "login_failed",
            ip=ip,
            meta={"login_sha": sha256(login_id).hex()[:16]},
        )
        await session.commit()
        raise InvalidCredentials

    await limits.reset(limits.login_key(login_id))
    if record.totp_confirmed or record.requires_2fa:
        scope: Literal["verify", "enroll"] = "verify" if record.totp_confirmed else "enroll"
        token, ttl = issue_mfa(user_id=record.user_id, scope=scope)
        status: MfaOutcome = "mfa_required" if scope == "verify" else "mfa_enrollment_required"
        return MfaChallenge(status=status, mfa_token=token, expires_in=ttl)
    return await _start_session(session, user_id=record.user_id, amr=("pwd",), otp_at=None, ip=ip)


async def _mfa_failure(
    session: AsyncSession, keys: list[str], user_id: uuid.UUID, ip: str, action: str
) -> None:
    await limits.record_failure(keys)
    await repository.audit_event(
        session, action, table="user", row_id=user_id, actor=user_id, ip=ip
    )
    await session.commit()


async def verify_mfa(
    session: AsyncSession,
    *,
    mfa_token: str,
    code: str | None,
    recovery_code: str | None,
    ip: str,
) -> IssuedSession:
    try:
        claims = decode_mfa(mfa_token, scope="verify")
    except InvalidTokenError as error:
        raise InvalidToken from error
    user_id = claims.user_id
    keys = [limits.mfa_key(user_id), limits.mfa_ip_key(ip)]
    if wait := await limits.blocked_for(keys):
        raise RateLimited(wait)

    otp_at: int | None = None
    amr: tuple[str, ...]
    if code is not None:
        record = await repository.totp_get(session, user_id)
        if record is None or not record.confirmed or record.secret_enc is None or not record.key_id:
            await _mfa_failure(session, keys, user_id, ip, "mfa_failed")
            raise InvalidCode
        try:
            secret = decrypt_totp_secret(record.secret_enc, record.key_id, user_id=user_id)
        except SecretDecryptionError as error:
            await _mfa_failure(session, keys, user_id, ip, "mfa_failed")
            raise InvalidCode from error
        step = totp.matching_step(secret, code)
        if step is None or not await totp.claim_step(user_id, step):
            await _mfa_failure(session, keys, user_id, ip, "mfa_failed")
            raise InvalidCode
        amr, otp_at = ("pwd", "otp"), int(time.time())
    else:
        if recovery_code is None:  # схема запроса гарантирует ровно одно из двух
            raise InvalidCode
        digest = sha256(totp.normalize_recovery_code(recovery_code))
        if not await repository.recovery_code_use(session, user_id=user_id, code_hash=digest):
            await _mfa_failure(session, keys, user_id, ip, "mfa_failed")
            raise InvalidCode
        await session.commit()
        amr = ("pwd", "rcv")

    await limits.reset(limits.mfa_key(user_id))
    return await _start_session(session, user_id=user_id, amr=amr, otp_at=otp_at, ip=ip)


async def refresh(session: AsyncSession, *, refresh_token: str) -> IssuedSession:
    new_token = new_opaque_token()
    result = await repository.refresh_rotate(
        session, old_hash=sha256(refresh_token), new_hash=sha256(new_token)
    )
    await session.commit()
    if result.family_id is not None and result.status != "ok":
        # Семья отозвана (или была отозвана раньше): access-токены этой сессии больше не годятся.
        await denylist.deny([result.family_id])
    if result.status != "ok" or result.user_id is None or result.family_id is None:
        raise InvalidToken
    record = await principal_store.snapshot(result.user_id)
    if record is None:
        await denylist.deny([result.family_id])
        raise InvalidToken
    access_token, expires_in = issue_access(
        user_id=result.user_id, sid=result.family_id, role_ver=record.role_ver, amr=result.amr
    )
    # TTL нового токена сохраняет БД (срок предъявленного), поэтому cookie берёт его из роли.
    days = (
        get_settings().refresh_ttl_days_field
        if record.role_code in FIELD_ROLES
        else get_settings().refresh_ttl_days
    )
    return IssuedSession(
        access_token=access_token,
        expires_in=expires_in,
        refresh_token=new_token,
        refresh_max_age=days * 86400,
        me=me_response(record, amr=result.amr),
    )


async def logout(
    session: AsyncSession, *, refresh_token: str | None, access_sid: uuid.UUID | None
) -> None:
    families: set[uuid.UUID] = set()
    if refresh_token:
        found = await repository.refresh_logout(session, sha256(refresh_token))
        await session.commit()
        if found is not None:
            families.add(found[1])
    if access_sid is not None:
        families.add(access_sid)
    if families:
        await denylist.deny(families)


async def start_mfa_setup(
    session: AsyncSession, *, user_id: uuid.UUID, account: str
) -> tuple[str, str]:
    secret = totp.new_secret()
    blob, key_id = encrypt_totp_secret(secret, user_id=user_id)
    if not await repository.totp_set_pending(
        session, user_id=user_id, secret_enc=blob, key_id=key_id
    ):
        raise MfaAlreadyEnabled
    await session.commit()
    return secret, totp.provisioning_uri(secret, account)


async def confirm_mfa(
    session: AsyncSession, *, user_id: uuid.UUID, code: str, ip: str
) -> list[str]:
    keys = [limits.mfa_key(user_id), limits.mfa_ip_key(ip)]
    if wait := await limits.blocked_for(keys):
        raise RateLimited(wait)
    record = await repository.totp_get(session, user_id)
    if record is None or record.secret_enc is None or not record.key_id:
        raise MfaNotStarted
    if record.confirmed:
        raise MfaAlreadyEnabled
    try:
        secret = decrypt_totp_secret(record.secret_enc, record.key_id, user_id=user_id)
    except SecretDecryptionError as error:
        raise MfaNotStarted from error
    step = totp.matching_step(secret, code)
    if step is None or not await totp.claim_step(user_id, step):
        await limits.record_failure(keys)
        raise InvalidCode
    codes = totp.new_recovery_codes()
    hashes = [sha256(totp.normalize_recovery_code(item)) for item in codes]
    if not await repository.totp_confirm(session, user_id=user_id, code_hashes=hashes):
        raise MfaAlreadyEnabled
    await session.commit()
    await limits.reset(limits.mfa_key(user_id))
    return codes


async def start_session_after_enrollment(
    session: AsyncSession, *, user_id: uuid.UUID, ip: str
) -> IssuedSession:
    return await _start_session(
        session, user_id=user_id, amr=("pwd", "otp"), otp_at=int(time.time()), ip=ip
    )


async def change_password(
    session: AsyncSession, *, principal: Principal, current: str, new: str
) -> None:
    key = limits.password_key(principal.user_id)
    if wait := await limits.blocked_for([key]):
        raise RateLimited(wait)
    stored = await repository.password_hash(session, principal.user_id)
    if not await verify_password_async(stored, current):
        await limits.record_failure([key])
        raise InvalidCurrentPassword
    if current == new:
        raise PasswordUnchanged
    new_hash = await hash_password_async(new)
    families = await repository.change_password(
        session, user_id=principal.user_id, new_hash=new_hash, keep_family=principal.sid
    )
    await session.commit()
    await limits.reset(key)
    await denylist.deny(families)
