"""Session-cookie auth: login, logout, the guard, and what stays open."""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import DOCUMENTED_COOKIE_NAME
from app.core.config import get_settings
from app.main import create_app
from app.services.auth import verify_password
from tests.unit.conftest import TEST_PASSWORD

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
SESSION = "/api/v1/auth/session"
ITEMS = "/api/v1/items"


async def test_login_with_correct_password_sets_the_session_cookie(
    anon_client: AsyncClient,
) -> None:
    response = await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})

    assert response.status_code == 204
    assert DOCUMENTED_COOKIE_NAME in response.headers["set-cookie"]
    assert anon_client.cookies.get(DOCUMENTED_COOKIE_NAME)


async def test_session_cookie_is_httponly_lax_and_two_weeks_long(
    anon_client: AsyncClient,
) -> None:
    """The cookie's flags are the whole session-security story — pin them."""
    response = await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})

    set_cookie = response.headers["set-cookie"]
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "Max-Age=1209600" in set_cookie  # 14 days
    assert "secure" not in set_cookie  # plain HTTP by default (dev)


async def test_session_cookie_is_secure_when_https_only_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployments behind Caddy's TLS set HTTPS_ONLY and get a Secure cookie."""
    monkeypatch.setenv("AUTH__SESSION__HTTPS_ONLY", "true")
    get_settings.cache_clear()

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="https://test") as tls:
        response = await tls.post(LOGIN, json={"password": TEST_PASSWORD})

    assert response.status_code == 204
    assert "secure" in response.headers["set-cookie"]


async def test_login_with_wrong_password_is_rejected_and_slow(
    anon_client: AsyncClient,
) -> None:
    started = time.monotonic()
    response = await anon_client.post(LOGIN, json={"password": "nope"})
    elapsed = time.monotonic() - started

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid password"}
    assert DOCUMENTED_COOKIE_NAME not in anon_client.cookies
    # The route's anti-guessing delay; generous margin for slow CI.
    assert elapsed >= 0.25


async def test_login_with_a_malformed_stored_hash_returns_401_not_500(
    anon_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH__PASSWORD_HASH", "not-a-bcrypt-hash")
    get_settings.cache_clear()

    response = await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})

    assert response.status_code == 401


async def test_login_with_an_unparseable_body_returns_documented_400(
    anon_client: AsyncClient,
) -> None:
    """Found by Schemathesis: FastAPI answers 400, so the contract must say so."""
    response = await anon_client.post(
        LOGIN,
        content=b"\x0f\xff\xfe not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "detail" in response.json()


async def test_protected_route_rejects_anonymous_calls(
    anon_client: AsyncClient,
) -> None:
    response = await anon_client.get(ITEMS)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


async def test_protected_route_accepts_a_logged_in_client(
    client: AsyncClient,
) -> None:
    assert (await client.get(ITEMS)).status_code == 200


async def test_tampered_cookie_is_rejected(anon_client: AsyncClient) -> None:
    await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})
    anon_client.cookies.set(DOCUMENTED_COOKIE_NAME, "garbage.not-a-signature")

    response = await anon_client.get(ITEMS)

    assert response.status_code == 401


async def test_cookie_signed_with_another_key_is_rejected(
    anon_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cookie minted by an app with a different secret does not carry over."""
    await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})
    stolen = anon_client.cookies[DOCUMENTED_COOKIE_NAME]

    monkeypatch.setenv("AUTH__SESSION__SECRET_KEY", "a-completely-different-secret")
    get_settings.cache_clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        other.cookies.set(DOCUMENTED_COOKIE_NAME, stolen)
        response = await other.get(ITEMS)

    assert response.status_code == 401


async def test_health_is_open(anon_client: AsyncClient) -> None:
    assert (await anon_client.get("/health")).status_code == 200


async def test_session_endpoint_is_open_and_reflects_state(
    anon_client: AsyncClient,
) -> None:
    before = await anon_client.get(SESSION)
    assert before.status_code == 200
    assert before.json() == {"authenticated": False}

    await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})

    after = await anon_client.get(SESSION)
    assert after.status_code == 200
    assert after.json() == {"authenticated": True}


async def test_logout_ends_the_session(client: AsyncClient) -> None:
    assert (await client.post(LOGOUT)).status_code == 204

    assert (await client.get(SESSION)).json() == {"authenticated": False}
    assert (await client.get(ITEMS)).status_code == 401


async def test_logout_without_a_session_is_a_noop(anon_client: AsyncClient) -> None:
    assert (await anon_client.post(LOGOUT)).status_code == 204


@pytest.mark.parametrize(
    ("plain", "password_hash"),
    [
        ("anything", ""),
        ("anything", "$2b$04$notreallyahash"),
        ("anything", "plaintext"),
    ],
)
def test_verify_password_rejects_unusable_hashes(
    plain: str, password_hash: str
) -> None:
    assert verify_password(plain, password_hash) is False
