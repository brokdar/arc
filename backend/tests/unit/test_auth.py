"""Session-cookie auth: login, logout, the guard, and what stays open."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from app.api.deps import DOCUMENTED_COOKIE_NAME
from app.core.config import get_settings
from app.main import create_app
from app.services.auth import verify_password
from tests.unit.conftest import TEST_PASSWORD

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
SESSION = "/api/v1/auth/session"
#: Any endpoint on the guarded router; the guard is what is under test.
PROTECTED = "/api/v1/anchors"


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
    response = await anon_client.get(PROTECTED)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


async def test_protected_route_accepts_a_logged_in_client(
    client: AsyncClient,
) -> None:
    assert (await client.get(PROTECTED)).status_code == 200


async def test_tampered_cookie_is_rejected(anon_client: AsyncClient) -> None:
    await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})
    anon_client.cookies.set(DOCUMENTED_COOKIE_NAME, "garbage.not-a-signature")

    response = await anon_client.get(PROTECTED)

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
        response = await other.get(PROTECTED)

    assert response.status_code == 401


#: An arbitrary fixed wall-second. The clock-step tests pin both the signing
#: and the verifying instant so the outcome cannot depend on which side of a
#: second boundary the test happens to run on.
SIGNED_AT = 1_700_000_000


@contextmanager
def wall_clock_reads(second: int) -> Iterator[None]:
    """Freeze the session signer's wall clock at `second`.

    Patched on the class, not an instance: `SessionMiddleware` builds its own
    signer and never hands it out, so the class attribute is the only reachable
    seam.
    """
    with mock.patch.object(TimestampSigner, "get_timestamp", return_value=second):
        yield


async def test_session_survives_a_wall_clock_that_steps_backwards(
    anon_client: AsyncClient,
) -> None:
    """Issue #61: a backwards clock step used to log the athlete out mid-flow.

    The cookie is signed at one wall-second and verified one second earlier —
    exactly what a host time sync does when its step crosses a second boundary.
    itsdangerous calls that a `SignatureExpired` of age -1; the athlete saw a
    401 on a cookie signed at login with the whole 14 days still ahead of it.
    """
    with wall_clock_reads(SIGNED_AT):
        assert (
            await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})
        ).status_code == 204

    with wall_clock_reads(SIGNED_AT - 1):
        response = await anon_client.get(PROTECTED)

    assert response.status_code == 200


async def test_session_older_than_max_age_is_still_rejected(
    anon_client: AsyncClient,
) -> None:
    """Tolerating future timestamps must not loosen the 14-day expiry.

    The old side of the window: the clock only moves forward here. Its future
    side is `test_cookie_signed_beyond_the_clock_step_tolerance_is_rejected`.
    """
    with wall_clock_reads(SIGNED_AT):
        await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})

    max_age = get_settings().auth.session.max_age_seconds
    with wall_clock_reads(SIGNED_AT + max_age + 1):
        response = await anon_client.get(PROTECTED)

    assert response.status_code == 401


async def test_cookie_signed_beyond_the_clock_step_tolerance_is_rejected(
    anon_client: AsyncClient,
) -> None:
    """The tolerance is a window, not an escape hatch from `max_age`.

    An unbounded version of it would be one: the age check is skipped for
    *every* outstanding cookie whenever the verifier's clock sits behind the
    signing instant, so a host whose clock went back far enough — a restored
    snapshot, a dead RTC — would accept a cookie that expired weeks ago. Here
    the cookie is signed a full `max_age` ahead of the clock reading it, which
    is that host, and the answer must still be 401.
    """
    max_age = get_settings().auth.session.max_age_seconds
    with wall_clock_reads(SIGNED_AT + max_age):
        await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})

    with wall_clock_reads(SIGNED_AT):
        response = await anon_client.get(PROTECTED)

    assert response.status_code == 401


async def test_cookie_tampered_after_signing_is_still_rejected(
    anon_client: AsyncClient,
) -> None:
    """A forged payload carrying an intact-looking timestamp gets no tolerance.

    Distinct from the garbage-cookie test above: this one keeps a well-formed
    timestamp, so it gets all the way to where itsdangerous splits value from
    timestamp — and still raises `BadTimeSignature`, not `SignatureExpired`
    (`itsdangerous/timed.py`: a signature error is re-raised before the age
    arithmetic is ever reached). The clock-step tolerance hangs off
    `SignatureExpired` alone, so no clock, forwards or backwards, can put a
    tampered cookie anywhere near it.
    """
    await anon_client.post(LOGIN, json={"password": TEST_PASSWORD})
    payload, timestamp, signature = anon_client.cookies[DOCUMENTED_COOKIE_NAME].split(
        "."
    )
    anon_client.cookies.set(
        DOCUMENTED_COOKIE_NAME, f"{payload[:-1]}x.{timestamp}.{signature}"
    )

    response = await anon_client.get(PROTECTED)

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
    assert (await client.get(PROTECTED)).status_code == 401


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
