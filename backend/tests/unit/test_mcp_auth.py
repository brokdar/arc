"""Parsing and comparison rules for the MCP server's static bearer keys."""

import pytest
from sqlalchemy import String

from app.domain.actor import Actor
from app.mcp.auth import (
    MAX_LABEL_LENGTH,
    MIN_KEY_LENGTH,
    McpKey,
    Scope,
    parse_api_keys,
    verify_key,
)
from app.persistence.audit import AuditLogEntry

#: Width of the column every agent write lands in, read off the model so the
#: label bound cannot drift away from what has to hold it.
_ACTOR_TYPE = AuditLogEntry.__table__.c.actor.type
AUDIT_ACTOR_LENGTH = _ACTOR_TYPE.length or 0 if isinstance(_ACTOR_TYPE, String) else 0

# Realistic key material: hex-looking and exactly MIN_KEY_LENGTH long, so the
# near-miss cases below can shorten one of these by a character and still be
# testing the comparison rather than the length rule.
COACH_KEY = "a1b2c3d4" * 4
READONLY_KEY = "9f8e7d6c" * 4

# --- parse_api_keys: well-formed ---------------------------------------------


def test_parses_a_single_entry() -> None:
    assert parse_api_keys(f"coach:write:{COACH_KEY}") == [
        McpKey(label="coach", scopes=frozenset({Scope.WRITE}), key=COACH_KEY)
    ]


def test_parses_multiple_entries_in_order() -> None:
    keys = parse_api_keys(f"coach:write:{COACH_KEY},readonly:read:{READONLY_KEY}")

    assert keys == [
        McpKey(label="coach", scopes=frozenset({Scope.WRITE}), key=COACH_KEY),
        McpKey(label="readonly", scopes=frozenset({Scope.READ}), key=READONLY_KEY),
    ]


def test_strips_whitespace_around_entries_and_fields() -> None:
    keys = parse_api_keys(
        f"  coach : write : {COACH_KEY} ,\n readonly:read:{READONLY_KEY} "
    )

    assert keys == [
        McpKey(label="coach", scopes=frozenset({Scope.WRITE}), key=COACH_KEY),
        McpKey(label="readonly", scopes=frozenset({Scope.READ}), key=READONLY_KEY),
    ]


def test_parses_a_multi_scope_entry_to_the_scope_set() -> None:
    (key,) = parse_api_keys(f"coach:read+write:{COACH_KEY}")

    assert key.scopes == frozenset({Scope.READ, Scope.WRITE})


def test_scope_order_in_the_entry_does_not_matter() -> None:
    # A set has no order: write+read grants exactly what read+write does.
    assert (
        parse_api_keys(f"coach:write+read:{COACH_KEY}")[0].scopes
        == parse_api_keys(f"coach:read+write:{COACH_KEY}")[0].scopes
    )


def test_strips_whitespace_around_scope_tokens() -> None:
    (key,) = parse_api_keys(f"coach:read + write:{COACH_KEY}")

    assert key.scopes == frozenset({Scope.READ, Scope.WRITE})


@pytest.mark.parametrize(
    "raw",
    [
        f"coach:write:{COACH_KEY},",
        f"coach:write:{COACH_KEY}, ",
        f",coach:write:{COACH_KEY}",
        f"coach:write:{COACH_KEY},,readonly:read:{READONLY_KEY}",
    ],
    ids=["trailing", "trailing-space", "leading", "doubled"],
)
def test_skips_empty_entries(raw: str) -> None:
    labels = [key.label for key in parse_api_keys(raw)]

    assert "coach" in labels
    assert "" not in labels


@pytest.mark.parametrize(
    "raw", ["", "   ", ",", " , "], ids=["empty", "ws", "c", "wsc"]
)
def test_returns_no_keys_for_an_empty_value(raw: str) -> None:
    # Whether "no keys" is fatal is the caller's call (app.mcp.main exits 1).
    assert parse_api_keys(raw) == []


def test_scopes_hold_the_enum_not_bare_strings() -> None:
    (key,) = parse_api_keys(f"coach:read:{COACH_KEY}")

    assert key.scopes == frozenset({Scope.READ})
    assert all(scope in Scope for scope in key.scopes)


def test_accepts_a_key_of_exactly_the_minimum_length() -> None:
    key_material = "z" * MIN_KEY_LENGTH

    (key,) = parse_api_keys(f"coach:read:{key_material}")

    assert key.key == key_material


# --- parse_api_keys: malformed -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"coach:{COACH_KEY}", "malformed"),
        (COACH_KEY, "malformed"),
        (f"coach:write:{COACH_KEY[:16]}:{COACH_KEY[16:]}", "malformed"),
        (f"coach:write:{COACH_KEY}:", "malformed"),
        (f"coach:admin:{COACH_KEY}", "unknown scope"),
        (f"coach:READ:{COACH_KEY}", "unknown scope"),
        (f"coach::{COACH_KEY}", "empty scope token"),
        (f"coach:read+admin:{COACH_KEY}", "valid scopes are: read, write"),
        (f"coach:read+read:{COACH_KEY}", "more than once"),
        (f"coach:read+write+read:{COACH_KEY}", "more than once"),
        (f"coach:read+:{COACH_KEY}", "empty scope token"),
        (f"coach:+write:{COACH_KEY}", "empty scope token"),
        (f"coach:read+write+:{COACH_KEY}", "empty scope token"),
        (f":write:{COACH_KEY}", "empty label"),
        ("coach:write:", "empty key"),
        ("coach:write:   ", "empty key"),
        (f"coach:write:{COACH_KEY},coach:read:{READONLY_KEY}", "duplicate label"),
        (f"coach:write:{COACH_KEY[:-1]}", "at least"),
        ("coach:write:short", "at least"),
        (f"coach:write:change-me-{'x' * MIN_KEY_LENGTH}", "placeholder"),
        # The literal value shipped in .env.example must not boot the server.
        ("coach:write:change-me-random-hex", "placeholder"),
        (f"coach:write:{COACH_KEY},readonly:read:{COACH_KEY}", "reuses the key"),
    ],
    ids=[
        "too-few-colons",
        "no-colons",
        "key-contains-colon",
        "trailing-colon",
        "unknown-scope",
        "scope-is-case-sensitive",
        "empty-scope",
        "unknown-scope-in-set",
        "duplicate-scope",
        "duplicate-scope-across-set",
        "trailing-plus",
        "leading-plus",
        "trailing-plus-after-set",
        "empty-label",
        "empty-key",
        "whitespace-key",
        "duplicate-label",
        "key-one-char-too-short",
        "key-far-too-short",
        "placeholder-key",
        "env-example-placeholder-key",
        "duplicate-key-material",
    ],
)
def test_malformed_entries_raise_value_error(raw: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        parse_api_keys(raw)


# --- parse_api_keys: the label is an identity, not a comment -----------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("night coach", "whitespace or control characters"),
        ("coach\nadmin", "whitespace or control characters"),
        ("coach\tadmin", "whitespace or control characters"),
        ("coach\x07", "whitespace or control characters"),
        ("c" * (MAX_LABEL_LENGTH + 1), "at most"),
        ("athlete", "reserved"),
        ("system", "reserved"),
        ("Athlete", "reserved"),
        ("agent", "reserved"),
    ],
    ids=[
        "space",
        "newline",
        "tab",
        "control-character",
        "too-long",
        "athlete",
        "system",
        "athlete-cased",
        "agent",
    ],
)
def test_a_label_that_would_not_survive_being_written_down_is_refused(
    label: str, expected: str
) -> None:
    # The label becomes `agent:<label>` in every audit row this key writes, so
    # it has to be one token, short enough for the column, and not the name of
    # another kind of actor. A newline in it also splits a log line in two,
    # which is the cheap way to forge a trail.
    with pytest.raises(ValueError, match=expected):
        parse_api_keys(f"{label}:write:{COACH_KEY}")


def test_a_label_of_exactly_the_maximum_length_is_accepted() -> None:
    label = "c" * MAX_LABEL_LENGTH

    (key,) = parse_api_keys(f"{label}:write:{COACH_KEY}")

    assert key.label == label
    assert len(str(Actor.agent(key.label))) <= AUDIT_ACTOR_LENGTH


@pytest.mark.parametrize(
    ("raw", "secret", "expected"),
    [
        (f"coach:admin:{COACH_KEY}", COACH_KEY, "unknown scope"),
        (f"coach:read+admin:{COACH_KEY}", COACH_KEY, "unknown scope"),
        (f"coach:read+read:{COACH_KEY}", COACH_KEY, "more than once"),
        ("coach:write:super-secret-key", "super-secret-key", "at least"),
        (
            f"coach:write:change-me-super-secret-{'x' * MIN_KEY_LENGTH}",
            f"change-me-super-secret-{'x' * MIN_KEY_LENGTH}",
            "placeholder",
        ),
        (
            f"coach:write:{COACH_KEY},readonly:read:{COACH_KEY}",
            COACH_KEY,
            "reuses the key",
        ),
    ],
    ids=[
        "unknown-scope",
        "unknown-scope-in-set",
        "duplicate-scope",
        "too-short",
        "placeholder",
        "duplicate-key",
    ],
)
def test_error_message_does_not_leak_key_material(
    raw: str, secret: str, expected: str
) -> None:
    # These messages are logged by app.mcp.main, so they must name the entry,
    # never the secret.
    with pytest.raises(ValueError, match=expected) as excinfo:
        parse_api_keys(raw)

    assert secret not in str(excinfo.value)


# --- verify_key ---------------------------------------------------------------


@pytest.fixture
def keys() -> list[McpKey]:
    return parse_api_keys(f"coach:write:{COACH_KEY},readonly:read:{READONLY_KEY}")


def test_verify_returns_the_matching_key(keys: list[McpKey]) -> None:
    matched = verify_key(keys, READONLY_KEY)

    assert matched is not None
    assert matched.label == "readonly"
    assert matched.scopes == frozenset({Scope.READ})


def test_verify_keeps_an_early_match(keys: list[McpKey]) -> None:
    # The loop deliberately runs to completion rather than returning on the
    # first hit; a match on the first key must survive the later misses.
    matched = verify_key(keys, COACH_KEY)

    assert matched is not None
    assert matched.label == "coach"


def test_verify_rejects_an_unknown_key(keys: list[McpKey]) -> None:
    assert verify_key(keys, "nope") is None


@pytest.mark.parametrize(
    "presented",
    [
        COACH_KEY[:-1],
        COACH_KEY + "0",
        COACH_KEY[:-1] + "5",
        COACH_KEY.upper(),
        " " + COACH_KEY,
        COACH_KEY + " ",
        "",
    ],
    ids=["short", "long", "one-char-off", "case", "leading-ws", "trailing-ws", "empty"],
)
def test_verify_rejects_near_misses(keys: list[McpKey], presented: str) -> None:
    # Exact, whole-string equality: no prefix match, no trimming, no folding.
    assert verify_key(keys, presented) is None


def test_verify_against_no_configured_keys() -> None:
    assert verify_key([], COACH_KEY) is None
    assert verify_key([], "") is None


def test_verify_handles_non_ascii_keys() -> None:
    # compare_digest rejects non-ASCII str arguments, so verify_key must
    # compare bytes — this raises TypeError if that ever regresses. The key is
    # padded to the minimum length so it survives parse_api_keys.
    non_ascii_key = "pässwörd" * 4
    keys = parse_api_keys(f"coach:read:{non_ascii_key}")

    assert verify_key(keys, non_ascii_key) is not None
    assert verify_key(keys, "password" * 4) is None
