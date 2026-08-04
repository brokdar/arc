"""Single-user session login. Thin layer over `app.services.auth`.

All three endpoints are mounted OUTSIDE the session-protected router: you
cannot log in, log out, or ask whether you are logged in while needing to be
logged in already.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Request, status

from app.api.schemas.auth import LoginRequest, SessionStatus
from app.core.config import get_settings
from app.core.exceptions import ErrorDetail, UnauthorizedError
from app.services.auth import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

#: Blunts online password guessing: every rejected login costs the caller this
#: much wall time. Deliberately in the route, not the service — verification
#: stays a pure function.
FAILED_LOGIN_DELAY_SECONDS = 0.3

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
    if not verify_password(
        payload.password, settings.auth.password_hash.get_secret_value()
    ):
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
