import pytest
from httpx import AsyncClient

from tests.auth_support import AUTH

pytestmark = pytest.mark.usefixtures("clean_redis")

REFRESH_COOKIE = "kut_refresh"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/login", {"identifier": "a\x00b@kut.test", "password": "x"}),
        ("/login", {"identifier": "\x00\x00\x00", "password": "x"}),
        ("/login", {"identifier": "x" * 5000, "password": "x"}),
        ("/login", {"identifier": 12345, "password": "x"}),
        ("/login", {"identifier": ["a@b.c"], "password": "x"}),
        ("/login", {"identifier": "a@b.c", "password": "x\x00y"}),
        ("/login", {"identifier": "😀@kut.test", "password": chr(0x202E)}),
        ("/login", {"identifier": "a@b.c"}),
        ("/login", []),
        ("/mfa/verify", {"mfa_token": "\x00" * 20, "code": "123456"}),
        ("/mfa/verify", {"mfa_token": "a.b.c" * 10, "recovery_code": "\x00"}),
        ("/mfa/verify", {"mfa_token": "." * 50, "code": "123456"}),
        ("/mfa/setup", {"mfa_token": "not-a-token"}),
        ("/mfa/confirm", {"code": "123456", "mfa_token": "x" * 5000}),
        ("/password", {"current_password": "x", "new_password": "y" * 20}),
    ],
)
async def test_hostile_input_never_causes_a_server_error(
    api: AsyncClient, path: str, body: object
) -> None:
    response = await api.post(f"{AUTH}{path}", json=body)

    assert 400 <= response.status_code < 500, (path, body, response.status_code)


async def test_a_login_with_control_characters_is_a_validation_error_not_a_lookup(
    api: AsyncClient,
) -> None:
    response = await api.post(
        f"{AUTH}/login", json={"identifier": "a\x00b@kut.test", "password": "x"}
    )

    assert response.status_code == 422


async def test_hostile_cookies_and_bearer_tokens_never_cause_a_server_error(
    api: AsyncClient,
) -> None:
    for value in ("", "x", "é" * 50, "a" * 10_000, "kut_refresh", "=;=", '"quoted"'):
        raw = f"{REFRESH_COOKIE}={value}".encode(
            "latin-1"
        )  # заголовки идут байтами, не только ASCII
        response = await api.post(f"{AUTH}/refresh", headers={"Cookie": raw})
        assert response.status_code == 401, value
    for header in ("Bearer", "Bearer ", "Basic abc", "Bearer a.b", "Bearer " + "A" * 20_000):
        response = await api.get(f"{AUTH}/me", headers={"Authorization": header})
        assert response.status_code == 401, header
