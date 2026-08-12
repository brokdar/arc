"""Static bearer-key auth for the MCP server.

Keys are configured as a single ``MCP__API_KEYS`` string of comma-separated
``label:scope[+scope]:key`` entries, e.g.::

    MCP__API_KEYS='coach:read+write:6f1c...,dashboard:read:9ab3...'

The label names the client (it shows up in logs and in the request's
authenticated identity), the scope set bounds what that client may do, and the
key is the bearer token itself. A single scope is a set of one, so existing
``label:scope:key`` entries parse unchanged. Scopes are not nested — ``write``
does not imply ``read`` — so a key that needs both carries both.

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

#: Separator between the scopes within one entry's scope field.
SCOPE_SEPARATOR = "+"

#: Fields per entry: label, scope set, key.
_FIELDS_PER_ENTRY = 3

_ENTRY_FORMAT_HINT = (
    "expected 'label:scope[+scope]:key' entries separated by commas, "
    "e.g. 'coach:read+write:<hex>,dashboard:read:<hex>'"
)

#: Shortest key accepted. 32 characters is what `openssl rand -hex 16` yields;
#: the documented recipe (`openssl rand -hex 32`) gives 64. Short keys are
#: brute-forceable and are almost always a hand-typed stand-in.
MIN_KEY_LENGTH = 32

#: Placeholder text carried by `.env.example`. Refusing it keeps a copied
#: example file from becoming a live credential.
_PLACEHOLDER_MARKER = "change-me"

#: Longest label accepted. The label is carried into every audit row as
#: ``agent:<label>`` and `AuditLogEntry.actor` is 120 characters, so anything
#: at or under this bound round-trips; a longer one would be a write that
#: fails at the database rather than at startup.
MAX_LABEL_LENGTH = 100

#: Labels that would be indistinguishable from another kind of actor in the
#: trail. `Actor` renders the athlete as ``athlete`` and the scheduler as
#: ``system``, and a key labelled either produces ``agent:athlete`` —
#: confusable enough to matter in the one place that answers "who changed
#: this".
_RESERVED_LABELS = frozenset({"athlete", "system", "agent"})


class Scope(StrEnum):
    """One thing a key is allowed to do.

    A key carries a *set* of scopes on its authenticated identity; individual
    tools enforce them (WP-8). Scopes are not nested — ``write`` does not imply
    ``read`` — so a tool asks for the scope it needs and the key's set must
    contain it.
    """

    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class McpKey:
    """One configured API key: who it is, what it may do, and its secret."""

    label: str
    scopes: frozenset[Scope]
    key: str


def _check_label(position: int, label: str) -> None:
    """Refuse a label that would not survive being written down.

    The label is not decoration: it becomes the agent's identity
    (`app.domain.actor.Actor.agent`), it is written to `audit_log.actor` as
    ``agent:<label>``, and it is what the athlete reads when asking who
    proposed something. So it has to be one token, short enough for the
    column, and not the name of another kind of actor. A label with a
    newline in it can also split a log line in two, which is the cheap way to
    forge a trail.

    Raises:
        ValueError: When the label holds whitespace or control characters, is
            longer than :data:`MAX_LABEL_LENGTH`, or is a reserved actor name.
            The message never contains key material.
    """
    if any(character.isspace() or not character.isprintable() for character in label):
        raise ValueError(
            f"MCP__API_KEYS entry {position} ({label!r}) has a label with "
            "whitespace or control characters; a label is one token and it is "
            "written into every audit row"
        )
    if len(label) > MAX_LABEL_LENGTH:
        raise ValueError(
            f"MCP__API_KEYS entry {position} has a label of {len(label)} "
            f"characters; labels must be at most {MAX_LABEL_LENGTH}, because "
            "every write this key makes stores 'agent:<label>'"
        )
    if label.casefold() in _RESERVED_LABELS:
        reserved = ", ".join(sorted(_RESERVED_LABELS))
        raise ValueError(
            f"MCP__API_KEYS entry {position} ({label!r}) uses a reserved "
            f"label; {reserved} name the other kinds of actor and a key called "
            "one of them makes the audit trail ambiguous"
        )


def _parse_scopes(position: int, label: str, raw_scope: str) -> frozenset[Scope]:
    """Parse one entry's scope field into a scope set.

    The field is one or more scopes joined by :data:`SCOPE_SEPARATOR`
    (``read``, ``write``, ``read+write``). Listing a scope twice is refused
    rather than collapsed: a duplicate is a typo for the other scope often
    enough that silently deduplicating would grant less than the operator
    meant to.

    Raises:
        ValueError: On an empty scope token, an unknown scope (the message
            lists the valid ones), or a scope listed twice. The message never
            contains key material.
    """
    scopes: set[Scope] = set()
    for token in (part.strip() for part in raw_scope.split(SCOPE_SEPARATOR)):
        if not token:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({label!r}) has an empty "
                f"scope token; scopes are joined with '{SCOPE_SEPARATOR}', "
                "e.g. 'read+write'"
            )
        try:
            scope = Scope(token)
        except ValueError as exc:
            valid = ", ".join(sorted(member.value for member in Scope))
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({label!r}) has unknown scope "
                f"{token!r}; valid scopes are: {valid}"
            ) from exc
        if scope in scopes:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({label!r}) lists scope "
                f"{scope.value!r} more than once"
            )
        scopes.add(scope)
    return frozenset(scopes)


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
        ValueError: On a malformed entry, a bad scope set (unknown, empty or
            duplicated scope), a key that is too short or still the
            `.env.example` placeholder, or a duplicate label or key. The
            message never contains the key material.
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
        _check_label(position, raw_label)
        if not raw_key:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({raw_label!r}) has an empty key"
            )

        scopes = _parse_scopes(position, raw_label, raw_scope)

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
        # the same secret shared by two labels/scope sets is a configuration
        # error, not a shorthand for "either identity".
        if raw_key in seen_keys:
            raise ValueError(
                f"MCP__API_KEYS entry {position} ({raw_label!r}) reuses the key "
                "of an earlier entry; every key must be distinct"
            )
        seen_keys.add(raw_key)

        keys.append(McpKey(label=raw_label, scopes=scopes, key=raw_key))

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
