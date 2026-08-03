"""Application error hierarchy and FastAPI exception handlers.

Services raise :class:`AppError` subclasses; the handlers translate them to
consistent JSON responses so endpoints never need try/except for domain
errors.
"""

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


class AppError(Exception):
    """Base class for domain errors carrying an HTTP status."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NotFoundError(AppError):
    """The requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """The request conflicts with existing state (e.g. duplicate)."""

    status_code = status.HTTP_409_CONFLICT


class ValidationError(AppError):
    """The request is semantically invalid beyond schema validation."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


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
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": _sanitize_for_json(exc.errors())},
        )
