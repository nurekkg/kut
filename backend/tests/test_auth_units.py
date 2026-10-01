"""Модули аутентификации без БД: ключи, шифрование, токены, пароли, TOTP, адрес клиента."""

import base64
import time
import uuid
from collections.abc import Iterator

import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from starlette.requests import Request

from app.core.config import get_settings
from app.main import create_app
from app.modules.auth import totp
from app.modules.auth.crypto import SecretDecryptionError, decrypt_totp_secret, encrypt_totp_secret
from app.modules.auth.deps import client_ip
from app.modules.auth.keys import (
    AuthConfigError,
    _key_map,
    get_jwt_keys,
    get_totp_keys,
    validate_auth_config,
)
from app.modules.auth.schemas import LoginRequest, PasswordChangeRequest
from app.modules.auth.security import (
    hash_password,
    needs_rehash,
    new_opaque_token,
    normalize_login,
    sha256,
    verify_password,
)
from app.modules.auth.tokens import (
    InvalidTokenError,
    decode_access,
    decode_mfa,
    issue_access,
    issue_mfa,
)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def clear_caches() -> None:
    get_settings.cache_clear()
    get_jwt_keys.cache_clear()
    get_totp_keys.cache_clear()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Меняет окружение и сбрасывает кэши настроек и ключей до и после теста."""
    clear_caches()
    yield monkeypatch
    monkeypatch.undo()
    clear_caches()


# --- ключи ---


@pytest.mark.parametrize("value", [None, "", "   ", "no-colon", "kid:", ":key", "a:b,a:c", "a:b,"])
def test_key_lists_with_a_wrong_shape_are_rejected(value: str | None) -> None:
    with pytest.raises(AuthConfigError):
        _key_map(value, name="X")


def test_key_lists_are_parsed_in_order_without_touching_the_material() -> None:
    assert _key_map("k1:AAA-_, k2:BBB", name="X") == {"k1": "AAA-_", "k2": "BBB"}


def test_jwt_keys_must_be_valid_base64_of_32_bytes_and_the_active_one_must_be_listed(
    env: pytest.MonkeyPatch,
) -> None:
    for value in ("k:***", f"k:{b64(b'short')}", f"k:{b64(bytes(33))}"):
        env.setenv("JWT_PRIVATE_KEYS", value)
        env.setenv("JWT_ACTIVE_KID", "k")
        clear_caches()
        with pytest.raises(AuthConfigError):
            get_jwt_keys()
    env.setenv("JWT_PRIVATE_KEYS", f"k:{b64(bytes(32))}")
    env.setenv("JWT_ACTIVE_KID", "other")
    clear_caches()
    with pytest.raises(AuthConfigError):
        get_jwt_keys()


def test_totp_keys_follow_the_same_rules(env: pytest.MonkeyPatch) -> None:
    env.setenv("TOTP_ENCRYPTION_KEYS", f"k:{b64(b'x' * 16)}")
    env.setenv("TOTP_ACTIVE_KEY_ID", "k")
    clear_caches()
    with pytest.raises(AuthConfigError):
        get_totp_keys()


def test_the_application_does_not_start_without_keys(env: pytest.MonkeyPatch) -> None:
    env.delenv("JWT_PRIVATE_KEYS")
    clear_caches()
    with pytest.raises(AuthConfigError, match="JWT_PRIVATE_KEYS"):
        create_app()

    env.undo()
    clear_caches()
    env.delenv("TOTP_ENCRYPTION_KEYS")
    clear_caches()
    with pytest.raises(AuthConfigError, match="TOTP_ENCRYPTION_KEYS"):
        create_app()


def test_production_refuses_the_development_example_keys_and_accepts_real_ones(
    env: pytest.MonkeyPatch,
) -> None:
    env.setenv("APP_ENV", "production")
    clear_caches()
    with pytest.raises(AuthConfigError, match="development example keys"):
        validate_auth_config()

    for variable in ("JWT_PRIVATE_KEYS", "TOTP_ENCRYPTION_KEYS"):
        env.setenv(variable, f"prod-1:{b64(bytes(range(32)))}")
    env.setenv("JWT_ACTIVE_KID", "prod-1")
    env.setenv("TOTP_ACTIVE_KEY_ID", "prod-1")
    clear_caches()
    validate_auth_config()


# --- шифрование секрета TOTP ---


def test_a_totp_secret_round_trips_and_is_bound_to_its_user() -> None:
    user, stranger = uuid.uuid4(), uuid.uuid4()
    secret = pyotp.random_base32()

    blob, key_id = encrypt_totp_secret(secret, user_id=user)

    assert decrypt_totp_secret(blob, key_id, user_id=user) == secret
    assert secret.encode() not in blob
    with pytest.raises(SecretDecryptionError):
        decrypt_totp_secret(
            blob, key_id, user_id=stranger
        )  # шифртекст из строки другого пользователя


def test_each_encryption_uses_a_fresh_nonce() -> None:
    user = uuid.uuid4()

    first, _ = encrypt_totp_secret("JBSWY3DPEHPK3PXP", user_id=user)
    second, _ = encrypt_totp_secret("JBSWY3DPEHPK3PXP", user_id=user)

    assert first != second and first[:12] != second[:12]


def test_tampered_short_or_unknown_key_blobs_are_rejected() -> None:
    user = uuid.uuid4()
    blob, key_id = encrypt_totp_secret("JBSWY3DPEHPK3PXP", user_id=user)
    flipped = blob[:-1] + bytes([blob[-1] ^ 1])

    for candidate, kid in ((flipped, key_id), (blob[:8], key_id), (blob, "no-such-key")):
        with pytest.raises(SecretDecryptionError):
            decrypt_totp_secret(candidate, kid, user_id=user)


def test_rotating_the_encryption_key_keeps_old_secrets_readable(env: pytest.MonkeyPatch) -> None:
    user = uuid.uuid4()
    old_blob, old_id = encrypt_totp_secret("JBSWY3DPEHPK3PXP", user_id=user)
    current = get_totp_keys()
    env.setenv(
        "TOTP_ENCRYPTION_KEYS",
        f"{old_id}:{b64(current.keys[old_id])},rotated:{b64(bytes(range(32)))}",
    )
    env.setenv("TOTP_ACTIVE_KEY_ID", "rotated")
    clear_caches()

    new_blob, new_id = encrypt_totp_secret("JBSWY3DPEHPK3PXP", user_id=user)

    assert new_id == "rotated" and old_id != new_id
    assert decrypt_totp_secret(old_blob, old_id, user_id=user) == "JBSWY3DPEHPK3PXP"
    assert decrypt_totp_secret(new_blob, new_id, user_id=user) == "JBSWY3DPEHPK3PXP"


# --- токены ---


def test_an_access_token_round_trips_with_its_claims() -> None:
    user, sid = uuid.uuid4(), uuid.uuid4()

    token, ttl = issue_access(user_id=user, sid=sid, role_ver=7, amr=("pwd", "otp"), otp_at=123)
    claims = decode_access(token)

    assert ttl == 600
    assert (claims.user_id, claims.sid, claims.role_ver) == (user, sid, 7)
    assert claims.amr == ("pwd", "otp") and claims.otp_at == 123
    assert claims.expires_at - int(time.time()) <= 600


def test_token_types_and_mfa_scopes_cannot_be_swapped() -> None:
    user = uuid.uuid4()
    access, _ = issue_access(user_id=user, sid=uuid.uuid4(), role_ver=1, amr=("pwd",))
    verify, _ = issue_mfa(user_id=user, scope="verify")
    enroll, _ = issue_mfa(user_id=user, scope="enroll")

    with pytest.raises(InvalidTokenError):
        decode_access(verify)
    with pytest.raises(InvalidTokenError):
        decode_mfa(access)
    with pytest.raises(InvalidTokenError):
        decode_mfa(verify, scope="enroll")
    assert decode_mfa(enroll, scope="enroll").user_id == user
    assert decode_mfa(verify).scope == "verify"


def sign(
    claims: dict[str, object], *, kid: str | None = None, key: Ed25519PrivateKey | None = None
) -> str:
    keys = get_jwt_keys()
    kid = kid or keys.active_kid
    return jwt.encode(claims, key or keys.private[kid], algorithm="EdDSA", headers={"kid": kid})


def valid_claims(**override: object) -> dict[str, object]:
    now = int(time.time())
    base: dict[str, object] = {
        "iss": "kut", "aud": "kut-api", "iat": now, "exp": now + 60, "jti": "j",
        "typ": "access", "sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "role_ver": 1,
        "amr": ["pwd"],
    }  # fmt: skip
    return {**base, **override}


def test_the_decoder_checks_issuer_audience_expiry_and_required_claims() -> None:
    assert decode_access(sign(valid_claims())).role_ver == 1

    bad = {
        "issuer": valid_claims(iss="someone-else"),
        "audience": valid_claims(aud="other-api"),
        "expired": valid_claims(exp=int(time.time()) - 5),
        "no sid": {k: v for k, v in valid_claims().items() if k != "sid"},
        "no exp": {k: v for k, v in valid_claims().items() if k != "exp"},
        "no jti": {k: v for k, v in valid_claims().items() if k != "jti"},
        "bad role_ver": valid_claims(role_ver="seven"),
        "bad sid": valid_claims(sid="not-a-uuid"),
    }
    for name, claims in bad.items():
        with pytest.raises(InvalidTokenError):
            decode_access(sign(claims))
        assert name


def test_a_token_signed_by_a_foreign_key_or_with_a_forged_algorithm_is_rejected() -> None:
    claims = valid_claims()

    forged = [
        sign(claims, key=Ed25519PrivateKey.generate()),
        jwt.encode(claims, None, algorithm="none"),
        jwt.encode(claims, "x" * 40, algorithm="HS256", headers={"kid": get_jwt_keys().active_kid}),
        sign(claims, kid="dev-1").rsplit(".", 1)[0] + "." + b64(bytes(64)),
    ]
    for token in forged:
        with pytest.raises(InvalidTokenError):
            decode_access(token)


def test_key_rotation_old_tokens_verify_until_the_old_key_is_retired(
    env: pytest.MonkeyPatch,
) -> None:
    old_token, _ = issue_access(user_id=uuid.uuid4(), sid=uuid.uuid4(), role_ver=1, amr=("pwd",))
    old = get_jwt_keys()
    old_id = old.active_kid
    seed = old.private[old_id].private_bytes_raw()
    new_seed = bytes(range(32))

    env.setenv("JWT_PRIVATE_KEYS", f"{old_id}:{b64(seed)},rotated:{b64(new_seed)}")
    env.setenv("JWT_ACTIVE_KID", "rotated")
    clear_caches()
    new_token, _ = issue_access(user_id=uuid.uuid4(), sid=uuid.uuid4(), role_ver=1, amr=("pwd",))

    assert jwt.get_unverified_header(new_token)["kid"] == "rotated"
    decode_access(old_token)  # старый ключ ещё в списке
    decode_access(new_token)
    env.setenv("JWT_PRIVATE_KEYS", f"rotated:{b64(new_seed)}")
    clear_caches()
    with pytest.raises(InvalidTokenError):
        decode_access(old_token)  # ключ выведен из обращения


# --- пароли ---


def test_passwords_are_hashed_with_argon2id_and_a_random_salt() -> None:
    first, second = hash_password("a-long-password-1"), hash_password("a-long-password-1")

    assert first.startswith("$argon2id$") and first != second
    assert verify_password(first, "a-long-password-1")
    assert not verify_password(first, "a-long-password-2")
    assert not needs_rehash(first)


def test_verifying_against_nothing_or_garbage_never_succeeds_and_never_raises() -> None:
    assert verify_password(None, "anything") is False
    assert verify_password("", "anything") is False
    assert verify_password("not-a-hash", "anything") is False
    assert verify_password("$argon2id$v=19$m=65536,t=3,p=4$AAAA$BBBB", "anything") is False


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        ("  User@Example.COM ", "user@example.com"),
        ("+996 (555) 12-34-56", "+996555123456"),
        ("996555123456", "+996555123456"),
        ("+996555123456", "+996555123456"),
    ],
)
def test_logins_are_normalised_the_way_the_database_stores_them(raw: str, normalised: str) -> None:
    assert normalize_login(raw) == normalised


def test_opaque_tokens_are_long_unique_and_hashed_with_sha256() -> None:
    tokens = {new_opaque_token() for _ in range(50)}

    assert len(tokens) == 50 and all(len(token) >= 43 for token in tokens)
    assert sha256("abc").hex() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_request_schemas_bound_the_password_and_login_lengths() -> None:
    LoginRequest(identifier="a@b.c", password="x")
    PasswordChangeRequest(current_password="x", new_password="y" * 10)
    for bad in (
        lambda: LoginRequest(identifier="ab", password="x"),
        lambda: LoginRequest(identifier="a@b.c", password=""),
        lambda: LoginRequest(identifier="a@b.c", password="x" * 257),
        lambda: LoginRequest(identifier="a@b.c", password="x", extra="field"),  # type: ignore[call-arg]
        lambda: PasswordChangeRequest(current_password="x", new_password="short"),
        lambda: PasswordChangeRequest(current_password="x", new_password="y" * 129),
    ):
        with pytest.raises(ValidationError):
            bad()


# --- TOTP ---


def test_totp_accepts_the_current_and_adjacent_steps_only() -> None:
    secret, now = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", 1_800_000_000.0
    generator = pyotp.TOTP(secret)
    step = int(now // 30)

    assert totp.matching_step(secret, generator.at(now), now=now) == step
    assert totp.matching_step(secret, generator.at(now - 30), now=now) == step - 1
    assert totp.matching_step(secret, generator.at(now + 30), now=now) == step + 1
    assert totp.matching_step(secret, generator.at(now - 60), now=now) is None
    assert totp.matching_step(secret, generator.at(now + 60), now=now) is None
    assert (
        totp.matching_step(secret, f" {generator.at(now)[:3]} {generator.at(now)[3:]} ", now=now)
        == step
    )
    for junk in ("", "12345", "1234567", "abcdef", "12345a", "١٢٣٤٥٦"):
        assert totp.matching_step(secret, junk, now=now) is None


async def test_a_totp_step_can_be_claimed_only_once(clean_redis: None) -> None:
    user = uuid.uuid4()

    assert await totp.claim_step(user, 100) is True
    assert await totp.claim_step(user, 100) is False
    assert await totp.claim_step(user, 101) is True
    assert await totp.claim_step(uuid.uuid4(), 100) is True


def test_recovery_codes_are_unique_well_formed_and_normalised() -> None:
    codes = totp.new_recovery_codes()

    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(code) == 19 and code.count("-") == 3 for code in codes)
    assert totp.normalize_recovery_code(f"  {codes[0].upper()} ") == codes[0]
    assert totp.normalize_recovery_code("ABCD 1234-ef56 7890") == "abcd-1234-ef56-7890"
    assert totp.normalize_recovery_code("abcd1234ef567890") == "abcd-1234-ef56-7890"
    assert totp.normalize_recovery_code("Not A Code") == "notacode"
    assert totp.provisioning_uri("JBSWY3DPEHPK3PXP", "a@b.c").startswith("otpauth://totp/Kut:")


# --- адрес клиента ---


def request_from(host: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (host, 1234), "method": "GET"})


def test_forwarded_for_is_ignored_unless_the_proxy_is_trusted(env: pytest.MonkeyPatch) -> None:
    spoofed = request_from("10.0.0.5", "6.6.6.6, 203.0.113.9")

    env.setenv("TRUST_FORWARDED_FOR", "false")
    clear_caches()
    assert client_ip(spoofed) == "10.0.0.5"

    env.setenv("TRUST_FORWARDED_FOR", "true")
    clear_caches()
    assert client_ip(spoofed) == "203.0.113.9"  # последний хоп дописал доверенный прокси
    assert client_ip(request_from("10.0.0.5")) == "10.0.0.5"


@pytest.mark.parametrize(
    ("app_env", "explicit", "trusted"),
    [
        ("development", None, True),  # API за прокси web
        ("test", None, False),
        ("production", None, False),  # без явного решения заголовку не верим
        ("production", "true", True),  # явный выбор оператора за доверенным прокси
        ("production", "false", False),
        ("development", "false", False),
    ],
)
def test_forwarded_for_is_trusted_by_default_only_in_development(
    env: pytest.MonkeyPatch, app_env: str, explicit: str | None, trusted: bool
) -> None:
    env.setenv("APP_ENV", app_env)
    if explicit is None:
        env.delenv("TRUST_FORWARDED_FOR", raising=False)
    else:
        env.setenv("TRUST_FORWARDED_FOR", explicit)
    clear_caches()

    assert get_settings().forwarded_for_trusted is trusted


def test_in_production_a_spoofed_forwarded_for_does_not_change_the_client_address(
    env: pytest.MonkeyPatch,
) -> None:
    env.setenv("APP_ENV", "production")
    env.delenv("TRUST_FORWARDED_FOR", raising=False)
    clear_caches()

    spoofed = request_from("198.51.100.4", "6.6.6.6")

    assert client_ip(spoofed) == "198.51.100.4"  # лимит попыток по IP не обойти заголовком
