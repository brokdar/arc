"""Static bearer-key auth for the MCP server.

Keys are configured as a single ``MCP__API_KEYS`` string of comma-separated
``label:scope:key`` entries, e.g.::

    MCP__API_KEYS='coach:write:6f1c...,readonly:read:9ab3...'

The label names the client (it shows up in logs and in the request's
authenticated identity), the scope bounds what that client may do, and the key
is the bearer token itself.

Key material has to earn its keep: a key must be at least
:data:`MIN_KEY_LENGTH` characters, must not contain the ``change-me``
placeholder text from ``.env.example``, and must not repeat another entry's key.
Labels must be unique too. Every one of those is a hard parse error, which
``app.mcp.main`` turns into exit 1 — a weak or copy-pasted key never reaches the
wire, and error messages never quote the key itself.

This module is deliberately framework-free — no FastMCP, no Starlette — so the
parsing and comparison rules can be tested in isolation and reused by whatever
adapter needs them. ``app.mcp.main`` adapts it to FastMCP's ``TokenVerifier``.
"""

import secrets
from dataclasses import dataclass
from enum import StrEnum

#: Separator between entries in the raw ``MCP__API_KEYS`` string.
ENTRY_SEPARATOR = ","

#: Separator between the fields of one entry.
FIELD_SEPARATOR = ":"

#: Fields per entry: label, scope, key.
_FIELDS_PER_ENTRY = 3

_ENTRY_FORMAT_HINT = (
    "expected 'label:scope:key' entries separated by commas, "
    "e.g. 'coach:write:<hex>,readonly:read:<hex>'"
)

#: Shortest key accepted. 32 characters is what `openssl rand -hex 16` yields;
#: the documented recipe (`openssl rand -hex 32`) gives 64. Short keys are
#: brute-forceable and are almost always a hand-typed stand-in.
MIN_KEY_LENGTH = 32

#: Placeholder text carried by `.env.example`. Refusing it keeps a copied
#: example file from becoming a live credential.
_PLACEHOLDER_MARKER = "change-me"


class Scope(StrEnum):
    """What a key is allowed to do.

    Scopes are carried on the authenticated identity; individual tools enforce
    them (WP-8). ``write`` is not a superset of ``read`` here — a tool asks for
    the scope it needs and the key must carry it.
    """

    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class McpKey:
    """One configured API key: who it is, what it may do, and its secret."""

    label: str
    scope: Scope
    key: str


def parse_api_keys(raw: str) -> list[McpKey]:
    """Parse the raw ``MCP__API_KEYS`` value into keys.

    Empty entries (and a trailing comma) are skipped, so an empty or
    whitespace-only string yields an empty list — callers decide whether that
    is fatal.

    Args:
        raw: The raw setting value.

    Returns:
        The configured keys, in the order they appear.

    Raises:
        ValueError: On a malformed entry, an unknown scope, a key that is too
            short or still the `.env.example` placeholder, or a duplicate label
            or key. The message never contains the key material.
    """
    keys: list[McpKey] = []
    seen_labels: set[str] = set()
    seen_keys: set[str] = set()

    for position, chunk in enumerate(raw.split(ENTRY_SEPARATOR), start=1):
        entry = chunk.strip()
        if not entry:
            continue

        if entry.count(FIELD_SEPARATOR) != _FIELDS_PER_ENTRY - 1:
            raise ValueError(
                f"MCP__API_KEYS entry {position} is malformed: {_ENTRY_FORMAT_HINT}. "
                f"Keys may not contain '{FIELD_SEPARATOR}'."
            )

        raw_label, raw_scope, raw_key = (
            field.strip() for field in entry.split(FIELD_SEPARATOR)
        )

        if not raw_label:
            raise ValueError(f"MCP__API_KEYS entry {position} has an empty label")
        if not raw_key:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({raw_label!r}) has an empty key"
            )

        try:
            scope = Scope(raw_scope)
        except ValueError as exc:
            valid = ", ".join(sorted(member.value for member in Scope))
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({raw_label!r}) has unknown scope "
                f"{raw_scope!r}; valid scopes are: {valid}"
            ) from exc

        # Key-material rules. Every message names the entry by position and
        # label only — never the key, since these errors get logged.
        if _PLACEHOLDER_MARKER in raw_key:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({raw_label!r}) still holds the "
                f"{_PLACEHOLDER_MARKER!r} placeholder from .env.example; "
                "generate a real key with 'openssl rand -hex 32'"
            )
        if len(raw_key) < MIN_KEY_LENGTH:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({raw_label!r}) has a key of "
                f"{len(raw_key)} characters; keys must be at least "
                f"{MIN_KEY_LENGTH} characters — generate one with "
                "'openssl rand -hex 32'"
            )

        if raw_label in seen_labels:
            raise ValueError(
                f"MCP__API_KEYS has a duplicate label {raw_label!r}; "
                "labels must be unique"
            )
        seen_labels.add(raw_label)

        # Duplicate key material would resolve order-dependently in
        # verify_key (its loop runs to completion and keeps the LAST match), so
        # the same secret shared by two labels/scopes is a configuration error,
        # not a shorthand for "either identity".
        if raw_key in seen_keys:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({raw_label!r}) reuses the key "
                "of an earlier entry; every key must be distinct"
            )
        seen_keys.add(raw_key)

        keys.append(McpKey(label=raw_label, scope=scope, key=raw_key))

    return keys


def verify_key(keys: list[McpKey], presented: str) -> McpKey | None:
    """Return the key matching ``presented``, or ``None``.

    Every configured key is compared with :func:`secrets.compare_digest` and the
    loop always runs to completion — no early return — so neither the number of
    comparisons nor their duration reveals which key (if any) matched.

    Args:
        keys: The configured keys.
        presented: The bearer token from the request.

    Returns:
        The matching key, or ``None`` when nothing matches.
    """
    presented_bytes = presented.encode("utf-8")
    matched: McpKey | None = None

    for candidate in keys:
        if secrets.compare_digest(candidate.key.encode("utf-8"), presented_bytes):
            matched = candidate

    return matched
