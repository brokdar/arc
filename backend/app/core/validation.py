"""Shared field-validation helpers."""

from typing import Annotated

from pydantic import AfterValidator, Field


def _postgres_safe_text(value: str) -> str:
    """Reject strings Postgres cannot store (found by Schemathesis fuzzing).

    Lone surrogates survive JSON parsing but fail UTF-8 encoding in the
    driver — they would otherwise surface as 500s instead of 422s.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("must be valid unicode") from exc
    return value


# The NUL-byte restriction lives in the JSON-schema `pattern` (not just a
# validator) so the OpenAPI contract documents it — NUL is invalid in
# Postgres text columns and would 500 at the driver otherwise.
PostgresText = Annotated[
    str,
    Field(pattern=r"^[^\x00]*$"),
    AfterValidator(_postgres_safe_text),
]
