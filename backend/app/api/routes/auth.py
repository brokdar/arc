"""Single-user session login. Thin layer over `app.services.auth`.

All three endpoints are mounted OUTSIDE the session-protected router: you
cannot log in, log out, or ask whether you are logged in while needing to be
logged in already.

Login costs real CPU (bcrypt at cost 12 is ~0.1-0.2s) and is therefore handled
carefully in two ways:

* Verification runs in a worker thread. Called inline it would block the event
  loop, so a handful of concurrent unauthenticated POSTs would stall every
  other request in the process — including `/health`, which container
  healthchecks watch.
* Attempts are serialized by `_login_lock`, which is held across both the
  verification and the post-failure delay. That serialization is what makes
  the delay a throttle: run concurrently, N guesses each wait out the same
  0.3s and cost an attacker 0.3s in total; queued, the cost accumulates to
  N * 0.3s. With exactly one user there is never a legitimate second login
  waiting behind the first.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Request, status
from starlette.concurrency import run_in_threadpool

from app.api.schemas.auth import LoginRequest, SessionStatus
from app.core.config import get_settings
from app.core.exceptions import ErrorDetail, UnauthorizedError
from app.services.auth import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

#: Wall time every rejected login costs the caller. A throttle only in
#: combination with `_login_lock` (see the module docstring). Deliberately in
#: the route, not the service — verification stays a pure function.
FAILED_LOGIN_DELAY_SECONDS = 0.3

#: Process-wide: one login attempt is verified and penalized at a time.
_login_lock = asyncio.Lock()

type Responses = dict[int | str, dict[str, Any]]
UNAUTHORIZED: Responses = {
    401: {"model": ErrorDetail, "description": "Invalid password"}
}
# FastAPI returns 400 (not 422) for bodies that fail to parse at all.
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}


@router.post(
    "/login",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=UNAUTHORIZED | BAD_BODY,
)
async def login(request: Request, payload: LoginRequest) -> None:
    """Exchange the configured password for a signed session cookie."""
    settings = get_settings()
    async with _login_lock:
        verified = await run_in_threadpool(
            verify_password,
            payload.password,
            settings.auth.password_hash.get_secret_value(),
        )
        if not verified:
            await asyncio.sleep(FAILED_LOGIN_DELAY_SECONDS)
            raise UnauthorizedError("Invalid password")
    request.session["auth"] = True


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> None:
    """Drop the session. A no-op (still 204) when there is none."""
    request.session.clear()


@router.get("/session")
async def read_session(request: Request) -> SessionStatus:
    """Report whether the caller is authenticated. Never rejects."""
    return SessionStatus(authenticated=bool(request.session.get("auth")))
