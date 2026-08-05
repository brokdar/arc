"""Decoding helpers for the domain's tagged-union JSON forms.

Workout structures, success criteria and purpose templates are all stored and
transported as JSON, and all three are decoded by hand rather than by a schema
library — `app.domain` is pure (D31), so there is no pydantic model to lean on
here even though the API layer has one.

Hand-written decoders rot into inconsistent error messages fast ("bad step",
"expected int", `KeyError: 'kind'`). These helpers make every message say the
same thing in the same shape — ``targets.power.pct_low: expected a number, got
'x'`` — because those messages are what a client sees: services wrap decoding
in `app.core.exceptions.domain_rules()`, which turns the `ValueError` into a
422 carrying the text verbatim.
"""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from enum import StrEnum
from typing import Any


def _at(path: str, message: str) -> ValueError:
    """Build the error, prefixed with where in the document it happened."""
    return ValueError(f"{path}: {message}" if path else message)


@contextmanager
def located(path: str) -> Generator[None]:
    """Prefix any ``ValueError`` raised inside with ``path``.

    The helpers above locate every *shape* error, but a node's semantic rules
    live in its ``__post_init__``, which knows the value and not where in the
    document it came from — so ``a ramp must prescribe at least one channel``
    would reach a client with no way to tell which of forty steps it meant.
    Wrapping the construction restores the contract the rest of this module
    promises, without every domain rule having to take a path argument.

    Wrap the **construction call only**, with the arguments already decoded: a
    message that already starts with ``path`` is passed through unprefixed, so
    an error from a nested codec keeps its own, deeper position rather than
    collecting one prefix per level on the way out.
    """
    try:
        yield
    except ValueError as exc:
        message = str(exc)
        if path and not message.startswith(path):
            raise ValueError(f"{path}: {message}") from exc
        raise


def as_mapping(value: Any, path: str = "") -> Mapping[str, Any]:
    """Require a JSON object."""
    if not isinstance(value, Mapping):
        raise _at(path, f"expected an object, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise _at(path, "object keys must be strings")
    return value


def as_sequence(value: Any, path: str = "") -> Sequence[Any]:
    """Require a JSON array (a bare string is not one)."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise _at(path, f"expected an array, got {type(value).__name__}")
    return value


def field(document: Mapping[str, Any], name: str, path: str = "") -> Any:
    """Return a required member of ``document``."""
    if name not in document:
        raise _at(path, f"missing required field {name!r}")
    return document[name]


def optional(document: Mapping[str, Any], name: str) -> Any:
    """Return an optional member of ``document``, or ``None``."""
    return document.get(name)


def as_str(value: Any, path: str) -> str:
    """Require a string."""
    if not isinstance(value, str):
        raise _at(path, f"expected a string, got {type(value).__name__}")
    return value


def as_int(value: Any, path: str) -> int:
    """Require an integer (``bool`` is not one, however much Python disagrees)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _at(path, f"expected an integer, got {type(value).__name__}")
    return value


def as_float(value: Any, path: str) -> float:
    """Require a finite number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _at(path, f"expected a number, got {type(value).__name__}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise _at(path, "expected a finite number")
    return number


def as_bool(value: Any, path: str) -> bool:
    """Require a boolean."""
    if not isinstance(value, bool):
        raise _at(path, f"expected a boolean, got {type(value).__name__}")
    return value


def as_enum[E: StrEnum](enum_class: type[E], value: Any, path: str) -> E:
    """Require one of ``enum_class``'s values, naming the alternatives if not."""
    text = as_str(value, path)
    try:
        return enum_class(text)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_class)
        raise _at(path, f"{text!r} is not one of: {allowed}") from exc


def no_extra_fields(
    document: Mapping[str, Any], allowed: frozenset[str], path: str
) -> None:
    """Reject members outside ``allowed``.

    A silently ignored field is a lost edit, and the client has no way to tell
    a typo'd key from an unsupported one.
    """
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise _at(path, f"unknown field(s): {', '.join(unknown)}")
