"""Parsing and comparison rules for the MCP server's static bearer keys."""

import pytest

from app.mcp.auth import McpKey, Scope, parse_api_keys, verify_key

# --- parse_api_keys: well-formed ---------------------------------------------


def test_parses_a_single_entry() -> None:
    assert parse_api_keys("coach:write:abc123") == [
        McpKey(label="coach", scope=Scope.WRITE, key="abc123")
    ]


def test_parses_multiple_entries_in_order() -> None:
    keys = parse_api_keys("coach:write:abc123,readonly:read:def456")

    assert keys == [
        McpKey(label="coach", scope=Scope.WRITE, key="abc123"),
        McpKey(label="readonly", scope=Scope.READ, key="def456"),
    ]


def test_strips_whitespace_around_entries_and_fields() -> None:
    keys = parse_api_keys("  coach : write : abc123 ,\n readonly:read:def456 ")

    assert keys == [
        McpKey(label="coach", scope=Scope.WRITE, key="abc123"),
        McpKey(label="readonly", scope=Scope.READ, key="def456"),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "coach:write:abc123,",
        "coach:write:abc123, ",
        ",coach:write:abc123",
        "coach:write:abc123,,readonly:read:def456",
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


def test_scope_is_the_enum_not_a_bare_string() -> None:
    (key,) = parse_api_keys("coach:read:abc123")

    assert key.scope is Scope.READ


# --- parse_api_keys: malformed -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("coach:abc123", "malformed"),
        ("abc123", "malformed"),
        ("coach:write:abc:123", "malformed"),
        ("coach:write:abc123:", "malformed"),
        ("coach:admin:abc123", "unknown scope"),
        ("coach:READ:abc123", "unknown scope"),
        ("coach::abc123", "unknown scope"),
        (":write:abc123", "empty label"),
        ("coach:write:", "empty key"),
        ("coach:write:   ", "empty key"),
        ("coach:write:abc123,coach:read:def456", "duplicate label"),
    ],
    ids=[
        "too-few-colons",
        "no-colons",
        "key-contains-colon",
        "trailing-colon",
        "unknown-scope",
        "scope-is-case-sensitive",
        "empty-scope",
        "empty-label",
        "empty-key",
        "whitespace-key",
        "duplicate-label",
    ],
)
def test_malformed_entries_raise_value_error(raw: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        parse_api_keys(raw)


def test_error_message_does_not_leak_key_material() -> None:
    with pytest.raises(ValueError, match="unknown scope") as excinfo:
        parse_api_keys("coach:admin:super-secret-key")

    assert "super-secret-key" not in str(excinfo.value)


# --- verify_key ---------------------------------------------------------------


@pytest.fixture
def keys() -> list[McpKey]:
    return parse_api_keys("coach:write:abc123,readonly:read:def456")


def test_verify_returns_the_matching_key(keys: list[McpKey]) -> None:
    matched = verify_key(keys, "def456")

    assert matched is not None
    assert matched.label == "readonly"
    assert matched.scope is Scope.READ


def test_verify_keeps_an_early_match(keys: list[McpKey]) -> None:
    # The loop deliberately runs to completion rather than returning on the
    # first hit; a match on the first key must survive the later misses.
    matched = verify_key(keys, "abc123")

    assert matched is not None
    assert matched.label == "coach"


def test_verify_rejects_an_unknown_key(keys: list[McpKey]) -> None:
    assert verify_key(keys, "nope") is None


@pytest.mark.parametrize(
    "presented",
    ["abc12", "abc1234", "abc124", "ABC123", " abc123", "abc123 ", ""],
    ids=["short", "long", "one-char-off", "case", "leading-ws", "trailing-ws", "empty"],
)
def test_verify_rejects_near_misses(keys: list[McpKey], presented: str) -> None:
    # Exact, whole-string equality: no prefix match, no trimming, no folding.
    assert verify_key(keys, presented) is None


def test_verify_against_no_configured_keys() -> None:
    assert verify_key([], "abc123") is None
    assert verify_key([], "") is None


def test_verify_handles_non_ascii_keys() -> None:
    # compare_digest rejects non-ASCII str arguments, so verify_key must
    # compare bytes — this raises TypeError if that ever regresses.
    keys = parse_api_keys("coach:read:pässwörd")

    assert verify_key(keys, "pässwörd") is not None
    assert verify_key(keys, "password") is None
