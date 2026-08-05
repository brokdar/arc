"""Shared field-validation helpers."""

from typing import Annotated, Any

from pydantic import AfterValidator, Field

#: Characters Postgres cannot store in a text or JSON value.
NUL = "\x00"


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


def _postgres_safe_document(document: dict[str, Any]) -> dict[str, Any]:
    """Reject NUL bytes and lone surrogates anywhere inside a JSON document.

    The same driver limits as :data:`PostgresText`, one level of nesting
    deeper: a free-form object is stored as JSONB, and a lone surrogate in a
    *key* three levels down fails at the driver exactly as one in a column
    would (found by Schemathesis fuzzing). There is no JSON-schema equivalent
    for "no NUL anywhere in this document", so unlike `PostgresText` this
    restriction cannot be documented in the contract — only enforced.
    """

    def check(node: Any) -> None:
        if isinstance(node, str):
            if NUL in node:
                raise ValueError("must not contain NUL bytes")
            _postgres_safe_text(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                check(key)
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(document)
    return document


#: A free-form JSON object that the database can actually store.
PostgresJsonObject = Annotated[dict[str, Any], AfterValidator(_postgres_safe_document)]
