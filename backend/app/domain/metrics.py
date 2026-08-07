"""Training metrics computed from a power series, and how a number explains itself.

Two things live here, and they are related by more than convenience.

**The metrics.** :func:`normalized_power`, :func:`intensity_factor` and
:func:`training_load` are the Coggan chain — NP, IF, TSS — as plain Python over
a sequence of watts. Plain Python because a four-hour ride is 14 400 samples
and that does not need a dataframe; the point of the module is that there is
**one** implementation. The planned number (`app.domain.prediction`) and the
recorded number (WP-5) come out of the same function, so they cannot disagree
by a few percent for short intervals — which is exactly what a closed-form
integral over step midpoints would do, and it would read as a systematic "you
did less than planned" bias in every interval session's adherence score. WP-5
may re-implement the *body* over a frame behind these exact signatures, and
must keep passing the fixtures in ``tests/unit/test_domain_metrics.py``.

**The explanation.** :class:`MetricExplanation` is the pattern every computed
number in this codebase follows from here on: the explanation of a number is
**data attached to the number**, not copy attached to a page. That is the only
form that survives the number being rendered in three places, and the only one
an MCP tool can hand to the coaching agent so the agent cites the same facts
the screen shows. A function that computes a metric builds the explanation
itself — it is the only code that knows which inputs and which assumptions
actually went in.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: The rolling window Coggan's normalized power is defined over, in seconds.
NP_WINDOW_S = 30

#: TSS is scaled so that one hour (3 600 s) at ``IF == 1`` is 100 points:
#: ``3600 / 36 == 100``.
TSS_SCALE = 36.0


@dataclass(frozen=True, slots=True)
class MetricExplanation:
    """Why a number is the number. Travels with it; not page copy.

    Args:
        formula: The arithmetic, written the way a human would read it.
        inputs: Named quantities that went in, already rendered — an anchor
            input names the **version's** value, provenance and effective date,
            never "the athlete's current FTP", because the number was computed
            against a frozen version and stays true when the athlete's FTP
            moves.
        assumptions: What the computation had to assume, in the order it
            assumed them. Empty when there were none.
        citation: Where the method comes from, when it comes from somewhere.
    """

    formula: str
    inputs: Mapping[str, str]
    assumptions: tuple[str, ...] = ()
    citation: str | None = None


def normalized_power(watts: Sequence[float], *, sample_hz: int = 1) -> float:
    """Coggan normalized power: 30 s rolling mean, 4th power, mean, 4th root.

    ``NP = ( mean( rolling_mean_30s(P)^4 ) )^(1/4)``

    The window is ``NP_WINDOW_S * sample_hz`` samples. Leading samples use a
    **shorter** window rather than being dropped: dropping them would shorten
    the series a short interval is averaged over, which moves NP for exactly
    the sessions NP matters most for.

    Requires a **uniformly sampled** series; over irregular samples the result
    is meaningless (WP-4 guarantees the grid).

    Args:
        watts: Power samples, evenly spaced at ``sample_hz``.
        sample_hz: Samples per second. At least 1.

    Returns:
        The normalized power in watts, or ``0.0`` for an empty series — a
        series with no samples carries no work, and returning 0.0 rather than
        raising keeps every caller from wrapping this in a length check that
        says the same thing.

    Raises:
        ValueError: When ``sample_hz`` is below 1.

    Reference: Allen & Coggan, *Training and Racing with a Power Meter*.
    """
    if sample_hz < 1:
        raise ValueError(f"sample_hz must be at least 1, got {sample_hz}")
    if not watts:
        return 0.0

    window = NP_WINDOW_S * sample_hz
    total = 0.0
    fourth_power_sum = 0.0
    for index, value in enumerate(watts):
        total += value
        if index >= window:
            total -= watts[index - window]
        rolling_mean = total / min(index + 1, window)
        fourth_power_sum += rolling_mean**4
    return (fourth_power_sum / len(watts)) ** 0.25


def intensity_factor(np_watts: float, ftp_watts: float) -> float:
    """Normalized power as a fraction of threshold: ``NP / FTP``.

    Args:
        np_watts: Normalized power, from :func:`normalized_power`.
        ftp_watts: The FTP the effort is judged against — the value of one
            *anchor version*, not a current-value lookup.

    Raises:
        ValueError: When ``ftp_watts`` is not above zero.
    """
    if ftp_watts <= 0:
        raise ValueError(f"ftp_watts must be above 0, got {ftp_watts}")
    return np_watts / ftp_watts


def training_load(duration_s: int, intensity_factor: float) -> float:
    """TSS = ``duration_s × IF² / 36``. One hour at FTP is 100.

    Args:
        duration_s: How long the effort lasted, in seconds.
        intensity_factor: From :func:`intensity_factor`.

    Raises:
        ValueError: When ``duration_s`` is negative.
    """
    if duration_s < 0:
        raise ValueError(f"duration_s must not be negative, got {duration_s}")
    return duration_s * intensity_factor**2 / TSS_SCALE
