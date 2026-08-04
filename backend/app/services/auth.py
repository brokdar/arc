"""Single-user password verification.

The application has exactly one user; there is no user table. The password is
configured as a bcrypt hash in ``AUTH__PASSWORD_HASH`` and checked here. Keep
this module pure and synchronous — the throttling of guesses (serialized
attempts plus a fixed penalty) lives in the route, not in the verification
function, and so does moving this CPU-bound call off the event loop.
"""

import bcrypt


def verify_password(plain: str, password_hash: str) -> bool:
    """Check a plaintext password against a bcrypt hash.

    Args:
        plain: The password as supplied by the client.
        password_hash: The configured bcrypt hash.

    Returns:
        True when the password matches. A missing or malformed hash (bcrypt
        raises ``ValueError``) is treated as "no match" rather than an error,
        so a misconfigured deployment rejects logins instead of 500-ing.
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
