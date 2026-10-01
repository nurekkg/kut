import hmac
import secrets
import time

import pyotp

from app.core.config import get_settings
from app.core.redis import get_redis

STEP_SECONDS = 30
WINDOW_STEPS = 1  # допускаем сдвиг часов на один шаг в обе стороны (ADR-001 §2)
RECOVERY_CODE_COUNT = 10
_REPLAY_TTL_SECONDS = (2 * WINDOW_STEPS + 2) * STEP_SECONDS


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=get_settings().totp_issuer)


def matching_step(secret: str, code: str, *, now: float | None = None) -> int | None:
    """Номер 30-секундного шага, которому соответствует код (с допуском ±1), иначе None."""
    cleaned = code.strip().replace(" ", "")
    # isascii: str.isdigit() принимает и «١٢٣٤٥٦», а hmac.compare_digest не сравнивает не-ASCII.
    if not (cleaned.isascii() and cleaned.isdigit() and len(cleaned) == 6):
        return None
    totp = pyotp.TOTP(secret)
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    found: int | None = None
    for step in range(current - WINDOW_STEPS, current + WINDOW_STEPS + 1):
        expected = totp.at(step * STEP_SECONDS)
        if hmac.compare_digest(expected, cleaned) and found is None:
            found = step
    return found


async def claim_step(user_id: object, step: int) -> bool:
    """Шаг принимается один раз: повторный код (в том числе перехваченный) отклоняется."""
    key = f"otp:{user_id}:{step}"
    claimed = await get_redis().set(key, "1", nx=True, ex=_REPLAY_TTL_SECONDS)
    return bool(claimed)


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Коды восстановления вида xxxxx-xxxxx (50 бит случайности каждый), показываются один раз."""
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(5)
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalize_recovery_code(value: str) -> str:
    return value.strip().lower().replace(" ", "")
