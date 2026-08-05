"""Parsing and comparison rules for the MCP server's static bearer keys."""

import pytest

from app.mcp.auth import MIN_KEY_LENGTH, McpKey, Scope, parse_api_keys, verify_key

# Realistic key material: hex-looking and exactly MIN_KEY_LENGTH long, so the
# near-miss cases below can shorten one of these by a character and still be
# testing the comparison rather than the length rule.
COACH_KEY = "a1b2c3d4" * 4
READONLY_KEY = "9f8e7d6c" * 4

# --- parse_api_keys: well-formed ---------------------------------------------


def test_parses_a_single_entry() -> None:
    assert parse_api_keys(f"coach:write:{COACH_KEY}") == [
        McpKey(label="coach", scope=Scope.WRITE, key=COACH_KEY)
    ]


def test_parses_multiple_entries_in_order() -> None:
    keys = parse_api_keys(f"coach:write:{COACH_KEY},readonly:read:{READONLY_KEY}")

    assert keys == [
        McpKey(label="coach", scope=Scope.WRITE, key=COACH_KEY),
        McpKey(label="readonly", scope=Scope.READ, key=READONLY_KEY),
    ]


def test_strips_whitespace_around_entries_and_fields() -> None:
    keys = parse_api_keys(
        f"  coach : write : {COACH_KEY} ,\n readonly:read:{READONLY_KEY} "
    )

    assert keys == [
        McpKey(label="coach", scope=Scope.WRITE, key=COACH_KEY),
        McpKey(label="readonly", scope=Scope.READ, key=READONLY_KEY),
    ]


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


def test_scope_is_the_enum_not_a_bare_string() -> None:
    (key,) = parse_api_keys(f"coach:read:{COACH_KEY}")

    assert key.scope is Scope.READ


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
        (f"coach::{COACH_KEY}", "unknown scope"),
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


@pytest.mark.parametrize(
    ("raw", "secret", "expected"),
    [
        (f"coach:admin:{COACH_KEY}", COACH_KEY, "unknown scope"),
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
    ids=["unknown-scope", "too-short", "placeholder", "duplicate-key"],
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
    assert matched.scope is Scope.READ


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
