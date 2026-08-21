"""The session cookie's signer, made tolerant of a clock that steps backwards.

Issue #61: the backend unit suite failed intermittently under `pytest -n auto`
with a mid-flow `401 {"detail": "Not authenticated"}` on a client that had
already logged in — a different test each run, and several workers failing at
the same instant. The chain:

1. `CLOCK_REALTIME` is not monotonic. This dev host (WSL2) steps it back
   ~85-130 ms about twice a minute when it re-syncs with the Windows host; a
   production host does the same, less often, on an NTP correction.
2. `SessionMiddleware` signs the cookie with `itsdangerous.TimestampSigner`,
   and signs it exactly once: starlette 1.3.1 only re-issues the cookie under
   `if session.modified and session` (`middleware/sessions.py`), and the reads
   `require_session` does never mark the session modified. arc writes the
   session once, at login. So the signed timestamp is fixed at login —
   `AUTH__SESSION__MAX_AGE_SECONDS` is an absolute lifetime from login, not a
   sliding one.
3. `TimestampSigner.unsign(..., max_age=...)` rejects a timestamp in the
   *future* as hard as one that is too old: its `age < 0` branch raises
   `SignatureExpired("Signature age -1 < 0 seconds")`.
4. `SessionMiddleware` catches `BadSignature` — which `SignatureExpired`
   subclasses — and silently starts an empty session, so `require_session`
   answers 401.

Because the timestamp is fixed at login, a step back only bites while the
clock is still behind the *login* instant — the fraction of a second the step
rewound. That is what made it intermittent, why it reads as a flaky test rather
than as the logout it is, and why the unit suite took it so much harder than
production would: every `client` fixture logs in milliseconds before the
request it then makes, so any step in that gap lands squarely in the window.
Production exposure is the same shape, login-adjacent and rare.

Tolerating a future-dated signature is not a security relaxation *as long as it
is bounded*. Only this server holds the signing key, so a client cannot mint a
timestamp at all, let alone move one forward; the timestamp is an expiry input,
not an authentication claim, and a tampered cookie fails signature validation
before any of this runs. But an unbounded tolerance would skip the `max_age`
check for every outstanding cookie whenever the verifier's clock sits behind
the signing instant — a restored VM snapshot or a dead RTC would resurrect any
cookie ever exfiltrated. Hence `CLOCK_STEP_TOLERANCE`.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal, overload

from itsdangerous import TimestampSigner
from itsdangerous.exc import SignatureExpired
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp

#: How far ahead of the verifier's clock a signature may be and still count as
#: fresh. Sized against the corrections that actually happen: the WSL2 steps
#: that motivated this are ~0.5 s, and an NTP slew or step correction is
#: sub-second to seconds — so 300 s is orders of magnitude of headroom, while
#: still being nothing next to the 14-day lifetime it must not swallow. The
#: deliberate trade: a backwards correction larger than this logs the athlete
#: out, which is exactly the pre-fix behaviour, for a case far rarer than the
#: sub-second steps this exists for — and re-login costs one password in a
#: single-user app.
CLOCK_STEP_TOLERANCE = timedelta(seconds=300)


class ClockStepTolerantTimestampSigner(TimestampSigner):
    """A `TimestampSigner` that treats a slightly future-dated signature as age 0."""

    # Upstream's overloads, repeated verbatim so the subclass stays
    # substitutable; the implementation takes the `bool` both of them narrow.
    @overload
    def unsign(
        self,
        signed_value: str | bytes,
        max_age: int | None = None,
        return_timestamp: Literal[False] = False,
    ) -> bytes: ...

    @overload
    def unsign(
        self,
        signed_value: str | bytes,
        max_age: int | None = None,
        return_timestamp: Literal[True] = True,
    ) -> tuple[bytes, datetime]: ...

    def unsign(
        self,
        signed_value: str | bytes,
        max_age: int | None = None,
        return_timestamp: bool = False,
    ) -> tuple[bytes, datetime] | bytes:
        """Unsign, forgiving a signature the verifier's clock has just missed.

        Delegates and then inspects the failure rather than reimplementing the
        age arithmetic, so signature validation, timestamp parsing and the
        too-old bound stay upstream's. `SignatureExpired` carries both the
        old-cookie case and the future-cookie one; they are told apart by
        `date_signed` against the clock — never by the exception's message,
        which is upstream prose that may be reworded.

        Raises:
            BadSignature: As `TimestampSigner.unsign` does — a bad signature, a
                missing or malformed timestamp, a signature genuinely older
                than `max_age`, or one dated further ahead than
                `CLOCK_STEP_TOLERANCE`.
        """
        # Bound once through a widened type: upstream's overloads key off a
        # *literal* `return_timestamp`, and this override forwards a `bool`.
        delegate: Callable[..., tuple[bytes, datetime] | bytes] = super().unsign
        try:
            return delegate(signed_value, max_age, return_timestamp)
        except SignatureExpired as expired:
            signed_at = expired.date_signed
            if signed_at is None:
                raise
            ahead = signed_at - self.timestamp_to_datetime(self.get_timestamp())
            if not timedelta(0) < ahead <= CLOCK_STEP_TOLERANCE:
                raise
            # Signed a plausible clock-step ahead of the moment we are reading
            # it: the clock stepped back between the two. The signature itself
            # already verified, so re-running without the age bound only skips
            # the check that the step invalidated — and only for a cookie whose
            # signing instant is within `CLOCK_STEP_TOLERANCE` of now, which is
            # what keeps this from being a way around `max_age`.
            return delegate(signed_value, None, return_timestamp)


class ClockStepTolerantSessionMiddleware(SessionMiddleware):
    """`SessionMiddleware` with the tolerant signer swapped in.

    Starlette constructs its signer inline in `__init__` and offers no
    injection point, so replacing the attribute afterwards is the only seam.
    Everything else — cookie name, flags, max-age, the `BadSignature` handling
    — is upstream's, unchanged.
    """

    # The passthrough signature costs the `add_middleware` call site its
    # ParamSpec type-check; mirroring upstream's eight parameters would buy that
    # back at the price of copying upstream's defaults here, where they would
    # drift silently on a starlette bump — so the type-check is the one given up.
    def __init__(self, app: ASGIApp, *args: Any, **kwargs: Any) -> None:
        super().__init__(app, *args, **kwargs)
        # Rebuilt from the key list upstream derived, so key rotation and any
        # future normalisation of `secret_key` keep working without a copy of
        # that logic here.
        self.signer = ClockStepTolerantTimestampSigner(self.signer.secret_keys)
