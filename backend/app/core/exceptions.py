"""Application error hierarchy and FastAPI exception handlers.

Services raise :class:`AppError` subclasses; the handlers translate them to
consistent JSON responses so endpoints never need try/except for domain
errors.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Error response body, as produced by the AppError handler.

    Reference it in endpoint ``responses={...}`` declarations so error
    statuses are part of the OpenAPI contract (and the generated frontend
    types).
    """

    detail: str


class ValidationErrorDetail(BaseModel):
    """422 response body — the one status with two shapes.

    A service raising :class:`ValidationError` produces a sentence; FastAPI's
    own request validation produces its list of per-field errors. Both are
    422s on the same endpoint, so the declared contract has to admit both, or
    a schema-conformance fuzzer is right to call one of them a lie.
    """

    detail: str | list[Any]


class AppError(Exception):
    """Base class for domain errors carrying an HTTP status."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, detail: str, headers: dict[str, str] | None = None) -> None:
        self.detail = detail
        #: Response headers the status requires (e.g. `Allow` on a 405).
        self.headers = headers
        super().__init__(detail)


class UnauthorizedError(AppError):
    """The caller has no valid session (or presented bad credentials)."""

    status_code = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    """The caller is authenticated and still may not do this.

    401 says "identify yourself"; this says "we know who you are, and this
    operation is not yours". The MVP has exactly one such rule and it is
    load-bearing: only `app.domain.actor.Actor.athlete` may declare a verdict
    or record a reason (WP-7.2), so the coaching agent presenting a perfectly
    valid write-scoped key is refused here rather than at the adapter.
    """

    status_code = status.HTTP_403_FORBIDDEN


class RedFlagError(ForbiddenError):
    """The athlete's illness/injury flag forbids this write (WP-8.4).

    A subclass rather than a status of its own because it is the same
    answer — "we know who you are, and this operation is not yours" — with a
    different subject: not the actor's identity but the state of the athlete.
    The coaching agent is refused *while the flag stands*, and the refusal has
    to say which change tripped it and why, because "add or intensify" is a
    rule the agent can plan around once it is told.
    """


class NotFoundError(AppError):
    """The requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """The request conflicts with existing state (e.g. duplicate)."""

    status_code = status.HTTP_409_CONFLICT


class ValidationError(AppError):
    """The request is semantically invalid beyond schema validation."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class RateLimitedError(AppError):
    """The caller has spent its write budget for the period (WP-8.3).

    The circuit breaker on the agent surface: a coaching agent in a loop can
    rewrite a training plan faster than the athlete can read the inbox, and
    the cap is what bounds the damage. 429 rather than 403 because it is
    temporary and the remedy is to wait — the message says until when.
    """

    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class UpstreamError(AppError):
    """A service arc depends on answered, and the answer was a failure.

    502 rather than the 422 these used to be folded into, because a 4xx says
    the request was wrong and there is nothing wrong with asking Dropbox for a
    folder listing while Dropbox is having a bad day.

    **A 502 body is athlete-facing prose, and the frontend prints it.** That is
    the split this status records, and it is the one place the rule lives:
    502 is arc relaying what a *named* upstream said, in a sentence written for
    somebody to read — "Dropbox says your account does not have access to
    this…" — so `frontend/lib/api-errors.ts` renders its `detail` exactly as it
    renders a 4xx's. 500 and every other 5xx are arc's own failure: the detail
    is a stack trace's leftovers rather than something anybody wrote for a
    reader, there is no remedy in it, and they keep the generic wording. So a
    `detail` here is drafted like any other sentence the athlete meets, and a
    diagnostic quoted into one reaches the screen.

    Distinguished from a *transport* failure, which stays a 422 saying arc
    could not reach the service at all — that one is the athlete's network or
    the operator's DNS, and it is the only case where "could not be reached"
    is a true sentence.
    """

    status_code = status.HTTP_502_BAD_GATEWAY


class MethodNotAllowedError(AppError):
    """The resource exists but the method is refused on principle.

    Not the same as an undefined route: FastAPI answers an unknown
    method+path combination with 404, so a resource that must say "this
    operation will never exist here" — an append-only anchor history, say —
    needs a real handler raising this.
    """

    status_code = status.HTTP_405_METHOD_NOT_ALLOWED


@contextmanager
def domain_rules() -> Iterator[None]:
    """Translate a domain rule violation into a 422.

    `app.domain` is pure, so it cannot raise `AppError` (that lives in
    `app.core`, which the purity contract forbids it): it signals a broken
    invariant with `ValueError`. Services wrap the construction of domain
    values in this, so "FTP must be between 30 and 700 W" reaches the client
    as a 422 with that sentence, rather than as a 500 with a stack trace.
    """
    try:
        yield
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _sanitize_for_json(obj: Any) -> Any:
    """Make an arbitrary object safely JSON-serializable.

    FastAPI's default 422 handler echoes the offending input back; if that
    input contains lone surrogates, serializing the RESPONSE crashes with a
    500 (found by Schemathesis fuzzing). Replace unencodable characters and
    repr() anything exotic.
    """
    if obj is None or isinstance(obj, bool | int | float):
        return obj
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, list | tuple):
        return [_sanitize_for_json(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): _sanitize_for_json(value) for key, value in obj.items()}
    return repr(obj)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers translating AppError subclasses to JSON responses."""

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": _sanitize_for_json(exc.errors())},
        )
