"""The athlete's daily wellness report: one row a day, and what it means.

**One entity, not two.** Increment 1 describes a "wellness series" and
"readiness markers" as separate things, and they overlap — resting HR and HRV
appear in both. Two tables would make "what did the athlete report on the 14th"
a join and a reconciliation, and would break the one-touchpoint-per-day promise
on the first day the athlete filled in one and not the other. So there is one
:class:`WellnessDay` per athlete-local date, holding objective markers,
subjective ratings, confounder tags and the free note; "readiness markers" is a
*view* over that row rather than a second store.

**Body weight lives here too, and is not an anchor type.** Weight needs an
effective date and a provenance like every other measurement, which reads as
"make it an `AnchorType`" — and issue #24 already argued the opposite case for
resting HR. Appending one anchor version per morning floods the history and
destroys the thing it is good at: seeing which *baseline* a prescription was
scaled against. The append-only property the acceptance criterion actually
wants is a property of the *series* (a new day never edits an earlier one), and
"the weight version governing date D" is then a pure function —
:func:`weight_in_force`.

**A day is corrigible; history is not.** "Appending never edits history" means
a new reading never rewrites an earlier one. It does not mean the athlete who
typed 6.5 h of sleep as 65 cannot fix it. One row per date, unique on the date,
corrigible in place, with every write audited before/after — which is cheaper
than a which-one rule over two rows for the same day.

**Arc stores and abstains.** Everything here is arithmetic over what the
athlete declared: a bound, a vocabulary, a date resolution, whether a
confounder the athlete named voids their own morning. Nothing here weighs those
facts against each other or against training — no readiness score, no threshold
verdict, no session downgrade. That line is enforced from the outside too: an
import-linter contract forbids `app.services.proposals`,
`app.services.guardrails` and `app.domain.proposals` from importing this
module, so wiring readiness into the constraint surface early is a build error
rather than a review comment.

Two conventions from `.claude/rules/backend-domain-units.md` bind every number
below: **a percentage is a fraction** (SpO2 is ``0.97``, never ``97``) and
**every range is half-open** ``[start, end)``.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.activity import parse_timezone


class WellnessProvenance(StrEnum):
    """Where a day's numbers came from. About the *value*, not the writer.

    The same split :class:`app.domain.anchors.Provenance` makes, with the two
    members a wellness reading can honestly claim. ``DEVICE_MEASURED`` is
    reserved: nothing writes it in Increment 1, and it exists now so that
    Increment 2's HealthKit path is a new caller rather than a migration of
    stored values.
    """

    ATHLETE_REPORTED = "athlete_reported"
    #: Reserved (Increment 2): read off a device export, not typed by a human.
    DEVICE_MEASURED = "device_measured"


class WellnessSource(StrEnum):
    """Who wrote the row. Distinct from :class:`WellnessProvenance`.

    Mirrors :class:`app.domain.anchors.AnchorSource`, and for the same reason:
    the agent records what it was told and may never sign as the athlete. A
    coach that cannot tell the athlete's report from its own transcription will
    one day cite its own echo back to them as evidence.
    """

    ATHLETE = "athlete"
    AGENT = "agent"


class WellnessPromptStatus(StrEnum):
    """The standing of one day's question: asked, answered, or closed unasked.

    The same three states :class:`app.domain.matching.EveningPromptStatus` has,
    and deliberately the same shape: a dated row, a stored deadline, and a
    terminal member the sweep writes. What matters is that ``EXPIRED`` is a
    *recorded fact* — "we asked and got no answer" — rather than the absence of
    a row, because a coach reading silence cannot tell the athlete who felt
    fine from the athlete nobody asked.
    """

    PENDING = "pending"
    ANSWERED = "answered"
    #: The day closed with no answer. Terminal: no follow-up is ever raised.
    EXPIRED = "expired"


#: The statuses a prompt never leaves. A raise over one of these is a no-op —
#: an answered day is not asked again and an expired one is not resurrected.
TERMINAL_PROMPT_STATUSES = frozenset(
    {WellnessPromptStatus.ANSWERED, WellnessPromptStatus.EXPIRED}
)


# Why `HrvMetric` exists, in a comment rather than in the docstring below.
#
# RMSSD and SDNN are different statistics over the same beat intervals — **not
# convertible, not on one scale** — and a baseline pooling them is not noisy,
# it is meaningless. The discriminator exists because Apple HealthKit exposes
# only `HKQuantityTypeIdentifierHeartRateVariabilitySDNN`: RMSSD is not in its
# public API at all, so Increment 2's ingest path cannot fill an RMSSD column
# however the field is named. Recording which statistic a number is costs
# nothing before the first write and costs a migration plus a re-derived
# baseline after it.
#
# This is also why the column is `hrv_ms` and not `hrv_rmssd_ms`: a column
# whose name asserts a statistic the value may not be is the kind of lie that
# survives every test.
#
# The HRV interpretation literature Increment 1 leans on — the natural-log
# transform, the smallest-worthwhile-change band at 0.5 x CV, the maturity bar
# — is calibrated on **RMSSD**. An SDNN series is stored and described
# faithfully; the statements bound to RMSSD stay bound to it.
#
# A comment and not a docstring because this enum is a **parameter type on an
# MCP tool**, and pydantic inlines a class docstring into the generated JSON
# schema as the member's `description` — which every client then pays for on
# every `tools/list`. Four paragraphs of reasoning made `record_wellness` the
# largest tool on the server. The operative sentence stays below, where the
# model needs it; the reasoning stays here, where the next editor does.
class HrvMetric(StrEnum):
    """Which HRV statistic a reading is. Not interchangeable: see above."""

    RMSSD = "rmssd"
    SDNN = "sdnn"


# Why `HrvContext` exists — a comment for the reason above.
#
# Apple Watch produces HRV two quite different ways: the overnight sleeping
# average, and opportunistic daytime spot samples whose frequency jumps when
# AFib History is enabled in Apple Health. A daytime spot RMSSD and an
# overnight mean have different distributions; averaging them produces a
# baseline belonging to neither, and one that shifts under the athlete the day
# they toggle a setting in an unrelated app.
#
# So a reading carries its context, and mixed-context series are never silently
# pooled. `PREFERRED_HRV_CONTEXT` is the default and the strongest commercial
# precedent.
class HrvContext(StrEnum):
    """How an HRV reading was taken. Baselines stay within one context."""

    SLEEPING = "sleeping"
    WAKING_SPOT = "waking_spot"
    MANUAL = "manual"


#: The context every read prefers and every default writes.
PREFERRED_HRV_CONTEXT = HrvContext.SLEEPING


class Confounder(StrEnum):
    """Things that were true about a night, in a vocabulary something can gate on.

    The list exists because of one documented failure: a deload week was once
    triggered by an alcohol artefact. Peripheral vasodilation produces the same
    temperature-up / resting-HR-down dissociation that otherwise reads as
    illness onset, and free text alone cannot gate anything — "two beers" in a
    note is invisible to every query that matters the next morning.

    Members are the athlete's own pre-check, copied out of their head rather
    than invented: :data:`INVALIDATES_MARKERS` says which of them make the
    morning's objective numbers *unusable* (logged, not acted on) as opposed to
    merely context. ``OTHER`` is legitimate and analyzable — reviewing the
    free-text and ``other`` volume and proposing new codes is the growth path,
    not a reason to leave the vocabulary open now.
    """

    ALCOHOL = "alcohol"
    LATE_MEAL = "late_meal"
    #: Sleep started more than ~2 h later than usual.
    POOR_SLEEP_TIMING = "poor_sleep_timing"
    SHORT_SLEEP = "short_sleep"
    #: Night minimum above ~20 degrees C.
    HOT_ROOM = "hot_room"
    TRAVEL = "travel"
    ALTITUDE = "altitude"
    ILLNESS_ONSET = "illness_onset"
    FIRST_SESSION_AFTER_LAYOFF = "first_session_after_layoff"
    HARD_SESSION_PREVIOUS_DAY = "hard_session_previous_day"
    MENSTRUAL_PHASE_NOTED = "menstrual_phase_noted"
    OTHER = "other"


#: Confounders that make the morning's **objective** markers unusable as
#: evidence for today. The alcohol case above is why this set exists; the other
#: four are the athlete's own, and each is a mechanism that moves resting HR,
#: HRV and wrist temperature without saying anything about training readiness.
#:
#: Everything not in here is *context*. Travel, a hard session yesterday, a
#: noted cycle phase, a late meal, altitude and an illness onset all change how
#: a reading should be read, and none of them voids it — an illness onset in
#: particular is a reading the coach most wants, not least.
INVALIDATES_MARKERS: frozenset[Confounder] = frozenset(
    {
        Confounder.ALCOHOL,
        Confounder.HOT_ROOM,
        Confounder.POOR_SLEEP_TIMING,
        Confounder.SHORT_SLEEP,
        Confounder.FIRST_SESSION_AFTER_LAYOFF,
    }
)


class BodyRegion(StrEnum):
    """Where soreness was felt. Keys of ``soreness_by_region``.

    A closed vocabulary for the same reason :class:`Confounder` is one: free
    text cannot answer "is the left knee thing recurring". Coarse on purpose —
    this is a phone form answered before coffee, not an anatomy chart.
    """

    NECK = "neck"
    SHOULDERS = "shoulders"
    UPPER_BACK = "upper_back"
    LOWER_BACK = "lower_back"
    CHEST = "chest"
    ARMS = "arms"
    FOREARMS = "forearms"
    ABDOMEN = "abdomen"
    HIPS = "hips"
    GLUTES = "glutes"
    QUADS = "quads"
    HAMSTRINGS = "hamstrings"
    CALVES = "calves"
    KNEES = "knees"
    ANKLES = "ankles"
    FEET = "feet"


class Polarity(StrEnum):
    """Which way a subjective scale points.

    Fatigue, soreness, stress and motivation are all 1-5 and they do not all
    point the same way: 5 motivation is good, 5 fatigue is not. A reader that
    assumes one direction is a bug nothing catches, because both readings are
    plausible numbers — so every scale declares its own, and no consumer may
    hardcode a direction.

    ``HIGHER_IS_NEITHER`` is the third answer and it is session RPE's: a 9 is a
    hard session, not a bad one. Forcing RPE into ``higher_is_worse`` to keep
    the enum binary would invent exactly the direction this table exists to
    stop being invented, and a reader would flag every genuinely hard session
    as a problem.
    """

    HIGHER_IS_BETTER = "higher_is_better"
    HIGHER_IS_WORSE = "higher_is_worse"
    HIGHER_IS_NEITHER = "higher_is_neither"


@dataclass(frozen=True, slots=True)
class SubjectiveScale:
    """One declared subjective scale: its range, direction and anchor words.

    Data, not code, and **served on both surfaces** (`GET /wellness/inputs`,
    `get_wellness_inputs`) so neither the UI nor the agent carries a private
    copy of what a 3 means.
    """

    #: The field this scale governs, as it is spelled on the wire.
    field: str
    #: Inclusive lowest legal value.
    low: int
    #: Inclusive highest legal value.
    high: int
    polarity: Polarity
    #: What each point means, in the athlete's words. Every point in
    #: ``[low, high]`` has one — a scale with unlabelled points is a scale two
    #: people answer differently.
    anchors: Mapping[int, str]
    #: One line on what the scale is asking about.
    prompt: str

    def __post_init__(self) -> None:
        """Refuse a scale that does not describe every point it admits."""
        if self.low >= self.high:
            raise ValueError(f"{self.field}: low must be below high")
        missing = [
            point
            for point in range(self.low, self.high + 1)
            if point not in self.anchors
        ]
        if missing:
            raise ValueError(
                f"{self.field}: no anchor descriptor for {missing} — every "
                "point on a declared scale needs words, or two people answer "
                "it differently"
            )

    def check(self, value: float) -> None:
        """Refuse a value off the scale, naming the range.

        Raises:
            ValueError: When ``value`` is outside ``[low, high]`` or not whole.
        """
        if value != int(value):
            raise ValueError(
                f"{self.field} is a whole number on a {self.low}-{self.high} "
                f"scale, got {value}"
            )
        if not self.low <= value <= self.high:
            raise ValueError(
                f"{self.field} must be between {self.low} and {self.high} "
                f"({self.polarity.value}), got {value:g}"
            )


#: Session RPE's scale, declared here with the wellness ones rather than beside
#: `SessionRow.rpe`. It has existed since #23 with no declared scale and no
#: descriptors, and putting it in this table buys three things: the UI shows
#: anchor words at entry instead of a bare 0-10 box, RPE and RIR become visibly
#: *distinct* scales rather than two numbers between 0 and 10, and one place
#: answers "what does a 7 mean" for every subjective input in the application.
#:
#: The bounds are duplicated from `app.services.activity` because the domain may
#: not import a service; `tests/unit/test_domain_wellness.py` fails if the two
#: drift.
SESSION_RPE_FIELD = "rpe"

#: A deliberate inconsistency, named so it does not read as an oversight:
#: wellness ratings are 1-5 while session RPE is 0-10. RPE answers "how hard was
#: that session" against Foster's decades-old anchored scale, which is worth
#: staying compatible with; a morning fatigue rating answered on a phone is more
#: consistently answered on five points than eleven. Both are declared here with
#: their own descriptors, which is what stops the difference being invisible.
SUBJECTIVE_SCALES: Mapping[str, SubjectiveScale] = {
    scale.field: scale
    for scale in (
        SubjectiveScale(
            field="sleep_quality",
            low=1,
            high=5,
            polarity=Polarity.HIGHER_IS_BETTER,
            prompt="How well you slept, regardless of how long.",
            anchors={
                1: "Barely slept — awake for hours",
                2: "Broken, woke repeatedly",
                3: "Unremarkable",
                4: "Solid, one brief wake at most",
                5: "Straight through, woke rested",
            },
        ),
        SubjectiveScale(
            field="fatigue",
            low=1,
            high=5,
            polarity=Polarity.HIGHER_IS_WORSE,
            prompt="How tired you feel this morning, before doing anything.",
            anchors={
                1: "Fresh",
                2: "Slightly flat",
                3: "Noticeably tired but functional",
                4: "Heavy — stairs are work",
                5: "Wiped out",
            },
        ),
        SubjectiveScale(
            field="soreness",
            low=1,
            high=5,
            polarity=Polarity.HIGHER_IS_WORSE,
            prompt="Overall muscle soreness. Regions go in soreness_by_region.",
            anchors={
                1: "None",
                2: "Aware of it when I move",
                3: "Sore, does not limit movement",
                4: "Sore enough to change how I move",
                5: "Painful to load",
            },
        ),
        SubjectiveScale(
            field="stress",
            low=1,
            high=5,
            polarity=Polarity.HIGHER_IS_WORSE,
            prompt="Life stress outside training, right now.",
            anchors={
                1: "Calm",
                2: "Mild background pressure",
                3: "Busy but manageable",
                4: "Under real pressure",
                5: "Overwhelmed",
            },
        ),
        SubjectiveScale(
            field="motivation",
            low=1,
            high=5,
            polarity=Polarity.HIGHER_IS_BETTER,
            prompt="How much you want to train today.",
            anchors={
                1: "Would rather not",
                2: "Reluctant",
                3: "Neutral — would do it because it is planned",
                4: "Keen",
                5: "Itching to go",
            },
        ),
        SubjectiveScale(
            field=SESSION_RPE_FIELD,
            low=0,
            high=10,
            polarity=Polarity.HIGHER_IS_NEITHER,
            prompt=(
                "Session RPE: how hard the whole session felt, judged after it. "
                "Not RIR, which counts reps left in a single set."
            ),
            anchors={
                0: "Rest",
                1: "Very, very easy",
                2: "Easy",
                3: "Moderate",
                4: "Somewhat hard",
                5: "Hard",
                6: "Hard+",
                7: "Very hard",
                8: "Very hard+",
                9: "Very, very hard",
                10: "Maximal — could not have done more",
            },
        ),
    )
}

#: The subjective wellness fields, in form order. Session RPE is in
#: :data:`SUBJECTIVE_SCALES` but not here: it belongs to a session, not to a
#: morning, and it is not written through the wellness surface.
SUBJECTIVE_FIELDS: tuple[str, ...] = (
    "sleep_quality",
    "fatigue",
    "soreness",
    "stress",
    "motivation",
)

#: The objective markers — numbers a device produced or a scale read. The
#: late-entry asymmetry (:func:`is_late_entry`) turns on this split: a watch
#: measured these on the day, and the date they were typed into arc says
#: nothing about the measurement.
OBJECTIVE_FIELDS: tuple[str, ...] = (
    "sleep_duration_s",
    "resting_hr_bpm",
    "hrv_ms",
    "respiratory_rate_brpm",
    "spo2",
    "wrist_temperature_delta_c",
    "weight_kg",
)


class InputTier(StrEnum):
    """How much a consumer should want an input, as data rather than as prose.

    Graceful degradation is a promise nothing enforces if the tier lives in a
    document: every manual input is tiered and every consumer defines its
    absent-input behaviour, so the tier is served
    (`GET /wellness/inputs`, `get_wellness_inputs`) and the UI orders the form
    by it while the agent learns what is worth asking for.

    **Nothing is ``REQUIRED``** — see :data:`INPUT_TIERS`. The member exists so
    that a later input which genuinely cannot be omitted has a word for it.
    """

    REQUIRED = "required"
    VALUABLE = "valuable"
    OPTIONAL = "optional"


#: One entry per writable wellness field. Nothing is ``REQUIRED``: this is
#: designed for the real athlete rather than the compliant one, and a required
#: daily input turns a missed morning into a failure state — which is how a
#: capture surface stops being answered at all.
#:
#: The six ``VALUABLE`` ones are the inputs the coach's morning question
#: actually turns on: two markers that move together (D7e's HRV/resting-HR
#: pair), how long the athlete slept, the weight that scales watts per
#: kilogram, and the two subjective readings the literature says weigh at least
#: as heavily as HRV.
INPUT_TIERS: Mapping[str, InputTier] = {
    "sleep_duration_s": InputTier.VALUABLE,
    "sleep_start_local": InputTier.OPTIONAL,
    "sleep_end_local": InputTier.OPTIONAL,
    "sleep_quality": InputTier.OPTIONAL,
    "resting_hr_bpm": InputTier.VALUABLE,
    "hrv_ms": InputTier.VALUABLE,
    "hrv_metric": InputTier.OPTIONAL,
    "hrv_context": InputTier.OPTIONAL,
    "respiratory_rate_brpm": InputTier.OPTIONAL,
    "spo2": InputTier.OPTIONAL,
    "wrist_temperature_delta_c": InputTier.OPTIONAL,
    "weight_kg": InputTier.VALUABLE,
    "fatigue": InputTier.VALUABLE,
    "soreness": InputTier.OPTIONAL,
    "stress": InputTier.OPTIONAL,
    "motivation": InputTier.VALUABLE,
    "soreness_by_region": InputTier.OPTIONAL,
    "confounders": InputTier.OPTIONAL,
    "note": InputTier.OPTIONAL,
}

#: Plausibility bounds per numeric field, inclusive at both ends.
#:
#: **Typo guards, not clinical limits**, in the tradition of
#: `app.domain.activity.check_temperature` — arc makes no medical judgement and
#: says so. An SpO2 of 97 (rather than 0.97) or a sleep duration of 65 (hours
#: read as seconds) would otherwise poison every rolling mean derived from it,
#: and unlike a wrong ride file nobody ever re-reads a morning to check.
#:
#: They live here rather than in the API schema because the MCP tool does not
#: pass through that schema: a bound that lived only there let a dry run
#: validate what the write then refused (issue #17).
BOUNDS: Mapping[str, tuple[float, float]] = {
    # 0 is legal and means "did not sleep", which is a real answer.
    "sleep_duration_s": (0.0, 86_400.0),
    "resting_hr_bpm": (20.0, 120.0),
    "hrv_ms": (1.0, 300.0),
    "respiratory_rate_brpm": (4.0, 40.0),
    # A fraction, per the units rule: 0.97, never 97.
    "spo2": (0.70, 1.0),
    # Deviation from the device's own baseline — what a watch actually reports.
    "wrist_temperature_delta_c": (-5.0, 5.0),
    "weight_kg": (30.0, 250.0),
}

#: Longest the free note may be. A column width *and* a domain rule, for the
#: reason `MAX_PROTOCOL_CHARS` is both.
MAX_NOTE_CHARS = 1000

#: How late a reading may be entered before the *subjective* half of it counts
#: as recalled rather than reported (see :func:`is_late_entry`).
#:
#: A domain constant and deliberately not a setting: it is a statement about
#: human recall that must mean the same thing in every deployment and every
#: test. Two days, because nobody accurately recalls last month's Tuesday
#: motivation and almost everybody can tell you about the night before last.
WELLNESS_LATE_ENTRY_DAYS = 2

#: Most days one batch write may carry. A year in one call — the natural unit
#: of "here is my history" — and a bound on user-supplied input, which is where
#: this codebase already puts such bounds (`MAX_STRENGTH_GROUPS`, `MAX_LIMIT`),
#: rather than a knob an operator turns up until something falls over.
MAX_BACKFILL_DAYS = 366


def wellness_day_date(sleep_end: dt.datetime, tz: str) -> dt.date:
    """The wellness day an overnight reading belongs to: the **wake** day.

    The calendar date on which the sleep *ended*, in the athlete's timezone. A
    night that begins 23:30 on the 12th and ends 07:00 on the 13th is the
    **13th**, which is what the athlete means when they read the numbers over
    coffee and call them "this morning's", and what both Athlytic and Apple
    Vitals mean by a recovery figure "set each morning".

    **This is deliberately the opposite of
    `app.domain.activity.session_date`**, which attributes a completed session
    to the day it *started*, so a ride running past midnight belongs to the day
    it began. The two answer different questions: a session is an event with a
    start, and a wellness day is a report about a morning. Applying the session
    rule here by reflex would land an overnight HRV average on the day before
    the one the athlete reads it on, every readiness read would look at
    yesterday's row, and nothing would notice. The rule is stated at both ends
    and each names the other for exactly that reason.

    Args:
        sleep_end: When the sleep ended, aware (any zone).
        tz: The athlete's timezone; see
            `app.domain.activity.parse_timezone` for the accepted forms.
            One athlete means one local clock — a second source of "what day is
            it" is how the plan and the wellness series come to disagree about
            Tuesday.

    Raises:
        ValueError: When ``sleep_end`` is naive, or ``tz`` is unresolvable.
    """
    if sleep_end.tzinfo is None:
        raise ValueError(
            "wellness_day_date needs an aware moment; a naive one would "
            "silently be read as local time on whichever machine ran this"
        )
    return sleep_end.astimezone(parse_timezone(tz)).date()


def entry_lag_days(local_date: dt.date, entered_at: dt.datetime, tz: str) -> int:
    """How many days after the day it describes a reading was entered.

    Zero for a same-day entry, negative for nothing (a future date is refused
    on every write path). ``entered_at`` is resolved to the athlete's own
    calendar first, so a reading typed at 00:30 local is one day late rather
    than two because the server happened to be on UTC.

    Raises:
        ValueError: When ``entered_at`` is naive, or ``tz`` is unresolvable.
    """
    if entered_at.tzinfo is None:
        raise ValueError("entered_at must be timezone-aware")
    return (entered_at.astimezone(parse_timezone(tz)).date() - local_date).days


def is_late_entry(local_date: dt.date, entered_at: dt.datetime, tz: str) -> bool:
    """Whether a reading was entered late enough for recall to be a problem.

    There is **no ``backfilled`` column**: ``local_date`` is the day the reading
    describes and ``created_at`` is when it was entered, so the lag is a
    subtraction and a stored boolean would be a denormalization of one — the
    kind that goes wrong the first time a row is corrected.

    What "late" then does is deliberately **asymmetric**, and the asymmetry is
    the whole reason backfill is worth building:

    * **objective** readings are never discounted. The watch measured the HRV
      on the day; the date it was typed into arc says nothing about the
      measurement, and discounting it would make an imported watch history
      worthless, which is the opposite of what importing is for.
    * **subjective** readings are marked ``subjective_recalled``, the flag
      rides along on every read, and subjective baselines exclude them from
      their maturity counts. Nobody accurately recalls last month's Tuesday
      motivation, and a baseline matured out of guesses is worse than a shorter
      honest one.
    """
    return entry_lag_days(local_date, entered_at, tz) > WELLNESS_LATE_ENTRY_DAYS


@dataclass(frozen=True, slots=True)
class MarkerStanding:
    """Whether a day's objective markers may be *acted on*, and why not.

    Storing confounders is not the same as applying them. Issue #24's procedure
    is a **pre-check**: some confounders make the morning's numbers unusable,
    and they get logged rather than acted on. A design that stores the tags and
    stops has moved the failure rather than fixed it — the coach reading a
    context block sees HRV two SD down and has to *remember* to look elsewhere
    for last night's beer. It will one day not remember, and the cost is a
    training week.

    So this rides on the **same object as the readings**, everywhere they are
    served. The numbers are never hidden: they are real, and they matter to the
    history. What is withheld is their standing as evidence *today*.
    """

    #: False when the athlete declared a confounder in :data:`INVALIDATES_MARKERS`.
    actionable: bool
    #: The declared confounders that voided the morning, in vocabulary order.
    invalidated_by: tuple[Confounder, ...]

    @property
    def statement(self) -> str:
        """One line a coach can read without decoding a boolean."""
        if self.actionable:
            return "recorded"
        named = ", ".join(member.value for member in self.invalidated_by)
        return f"recorded, not actionable: {named}"


def marker_standing(confounders: Iterable[Confounder]) -> MarkerStanding:
    """Resolve a day's declared confounders into a marker standing.

    A deterministic description of the athlete's own declaration, not an
    interpretation of their physiology — which is what keeps it on the
    descriptive side of the line this module abstains at.
    """
    declared = set(confounders)
    invalidating = tuple(
        member
        for member in Confounder
        if member in declared and member in INVALIDATES_MARKERS
    )
    return MarkerStanding(actionable=not invalidating, invalidated_by=invalidating)


@dataclass(frozen=True, slots=True)
class WellnessDay:
    """One athlete-local day of reported wellness, validated.

    Every value field is optional and **null means "not provided", never
    zero** — a day with sleep and nothing else is sleep plus seven absences,
    and no aggregate may treat an absence as data. There is no all-null day:
    :meth:`__post_init__` refuses one, the way `record_session_context` refuses
    a call with neither `rpe` nor `temperature_c`.

    Args:
        local_date: The day the reading *describes*. Derived for an overnight
            reading by :func:`wellness_day_date` — the wake day, not the day
            the sleep began.
        sleep_duration_s: Seconds slept, per the surface-wide duration
            convention. Hours are an edge rendering.
        sleep_start_local: Clock time the sleep began, **no date**. The date is
            the row's; storing a second one would create two answers to which
            day the night belongs to.
        sleep_end_local: Clock time the sleep ended, same reasoning.
        resting_hr_bpm: Resting heart rate.
        hrv_ms: Heart-rate variability in milliseconds. Requires
            ``hrv_metric`` and ``hrv_context`` — see :class:`HrvMetric`.
        hrv_metric: Which statistic ``hrv_ms`` is.
        hrv_context: How it was measured.
        respiratory_rate_brpm: Breaths per minute.
        spo2: Blood oxygen saturation as a **fraction** (0.97, never 97).
        wrist_temperature_delta_c: Deviation from the device's own baseline,
            which is what a watch actually reports — not an absolute.
        weight_kg: Body weight. Resolved for any date by
            :func:`weight_in_force`.
        sleep_quality: 1-5, :data:`SUBJECTIVE_SCALES`.
        fatigue: 1-5.
        soreness: 1-5, overall.
        stress: 1-5.
        motivation: 1-5.
        soreness_by_region: Per-region 1-5, keyed by :class:`BodyRegion`.
        confounders: What was true about the night.
        note: Free text. **Never parsed** — it is where the things the
            vocabulary has no word for go, and reading it as data would make
            the vocabulary pointless.
        provenance: Where the numbers came from.
        source: Who wrote the row.
    """

    local_date: dt.date
    sleep_duration_s: int | None = None
    sleep_start_local: dt.time | None = None
    sleep_end_local: dt.time | None = None
    resting_hr_bpm: int | None = None
    hrv_ms: float | None = None
    hrv_metric: HrvMetric | None = None
    hrv_context: HrvContext | None = None
    respiratory_rate_brpm: float | None = None
    spo2: float | None = None
    wrist_temperature_delta_c: float | None = None
    weight_kg: float | None = None
    sleep_quality: int | None = None
    fatigue: int | None = None
    soreness: int | None = None
    stress: int | None = None
    motivation: int | None = None
    soreness_by_region: Mapping[BodyRegion, int] = field(default_factory=dict)
    confounders: tuple[Confounder, ...] = ()
    note: str | None = None
    provenance: WellnessProvenance = WellnessProvenance.ATHLETE_REPORTED
    source: WellnessSource = WellnessSource.ATHLETE

    def __post_init__(self) -> None:
        """Enforce every rule that makes a day comparable with the next one."""
        for name, (low, high) in BOUNDS.items():
            value = getattr(self, name)
            if value is not None and not low <= value <= high:
                raise ValueError(
                    f"{name} must be between {low:g} and {high:g}, got {value:g}"
                )
        for name in SUBJECTIVE_FIELDS:
            value = getattr(self, name)
            if value is not None:
                SUBJECTIVE_SCALES[name].check(value)
        # Refused by name rather than left to blow up in `len()` or
        # `.items()`: an adapter that maps a cleared tag list to `None`
        # instead of to its empty value would otherwise reach a 500, and the
        # message would say nothing about which field.
        if self.confounders is None or self.soreness_by_region is None:
            raise ValueError(
                "confounders and soreness_by_region have an empty value, not "
                "an absent one: clear them with [] and {} rather than null"
            )
        region_scale = SUBJECTIVE_SCALES["soreness"]
        for region, rating in self.soreness_by_region.items():
            if not isinstance(region, BodyRegion):
                raise ValueError(
                    f"soreness_by_region keys must be body regions; "
                    f"{region!r} is not one of "
                    f"{', '.join(member.value for member in BodyRegion)}"
                )
            region_scale.check(rating)
        # The HRV triple travels together or not at all. A reading whose
        # statistic or context is unknown cannot join a baseline honestly, and
        # a context with no reading is a claim about nothing.
        stated = [self.hrv_metric, self.hrv_context]
        if self.hrv_ms is None and any(part is not None for part in stated):
            raise ValueError(
                "hrv_metric and hrv_context describe an HRV reading; give "
                "hrv_ms as well, or neither"
            )
        if self.hrv_ms is not None and any(part is None for part in stated):
            raise ValueError(
                "an HRV reading must state hrv_metric (rmssd or sdnn) and "
                "hrv_context (sleeping, waking_spot or manual): the two "
                "statistics are not on one scale and the two contexts are not "
                "one distribution, so a reading missing either cannot join a "
                "baseline honestly"
            )
        if len(set(self.confounders)) != len(self.confounders):
            raise ValueError("confounders must not repeat")
        if self.note is not None and len(self.note) > MAX_NOTE_CHARS:
            raise ValueError(
                f"note must be at most {MAX_NOTE_CHARS} characters, "
                f"got {len(self.note)}"
            )
        if self.is_empty:
            raise ValueError(
                "a wellness day must record something — give at least one "
                "reading, rating, confounder or note; a day with nothing on it "
                "is what an absent row already says"
            )

    @property
    def is_empty(self) -> bool:
        """Whether the day carries nothing at all."""
        return not any(
            (
                *(getattr(self, name) is not None for name in VALUE_FIELDS),
                bool(self.soreness_by_region),
                bool(self.confounders),
            )
        )

    @property
    def standing(self) -> MarkerStanding:
        """Whether this day's objective markers may be acted on."""
        return marker_standing(self.confounders)

    def check_not_future(self, today: dt.date) -> None:
        """Refuse a reading dated after ``today``.

        A future date is not a late entry: it is a typo or a confusion, and
        storing it would put a value in the baseline window before the day it
        describes has happened.

        ``today`` is passed in rather than read from a clock because this
        module is pure — the caller resolves the athlete's local date.

        Raises:
            ValueError: When the day is in the future, naming both dates.
        """
        if self.local_date > today:
            raise ValueError(
                f"that day has not happened yet (today is {today.isoformat()}): "
                "a wellness reading describes a morning that is over, and a "
                "future one would enter a baseline before the day it claims to "
                "report"
            )


#: Every column on a day that holds a single scalar value. Used for the
#: not-empty rule and for the completeness tests that keep
#: :data:`INPUT_TIERS`, :data:`BOUNDS` and the ORM row from drifting apart.
VALUE_FIELDS: tuple[str, ...] = (
    "sleep_duration_s",
    "sleep_start_local",
    "sleep_end_local",
    "resting_hr_bpm",
    "hrv_ms",
    "hrv_metric",
    "hrv_context",
    "respiratory_rate_brpm",
    "spo2",
    "wrist_temperature_delta_c",
    "weight_kg",
    "sleep_quality",
    "fatigue",
    "soreness",
    "stress",
    "motivation",
    "note",
)

#: Every field a caller may write, in a stable order. The vocabulary the MCP
#: tool checks its arguments against and the API schema enumerates, so a
#: misspelled field is refused by name rather than silently dropped.
WRITABLE_FIELDS: tuple[str, ...] = (
    *VALUE_FIELDS,
    "soreness_by_region",
    "confounders",
)


@dataclass(frozen=True, slots=True)
class WeightInForce:
    """The body weight governing a date, and the day it was recorded on."""

    weight_kg: float
    #: The ``local_date`` of the day the weight was reported on — the effective
    #: date. Carried because a watts-per-kilogram figure computed against a
    #: three-week-old weight should say so.
    effective_date: dt.date


def weight_in_force(days: Iterable[WellnessDay], on: dt.date) -> WeightInForce | None:
    """The weight version governing ``on``: the latest one on or before it.

    A pure fold over the series rather than a stored "current weight", which is
    what makes the append-only promise the acceptance criteria actually want
    hold for free: **appending a later weight never changes what an earlier
    date resolves to.** Record 78 kg on the 1st and 82 kg on the 20th and the
    10th still resolves to 78, because this looks backwards from the date
    asked about and nothing rewrites a stored answer.

    Returns:
        ``None`` when no weight was recorded on or before ``on`` — which is an
        answer, not a gap, and watts per kilogram is then **absent** rather
        than computed against a default. A default weight would produce a
        plausible number that is nobody's.
    """
    candidates = [
        day for day in days if day.weight_kg is not None and day.local_date <= on
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda day: day.local_date)
    # Narrowing for the type checker: `candidates` is filtered on exactly this.
    assert latest.weight_kg is not None  # noqa: S101
    return WeightInForce(weight_kg=latest.weight_kg, effective_date=latest.local_date)


def missing_dates(
    recorded: Iterable[dt.date], *, start: dt.date, end: dt.date
) -> tuple[dt.date, ...]:
    """The dates in the half-open range ``[start, end)`` that ``recorded`` lacks.

    Absence is reported rather than synthesized: a range read returns the days
    that exist and this says which ones do not, so no consumer has to decide
    whether a null-filled object means "nothing reported" or "reported as
    nothing". Half-open like every other range in this codebase.

    **``recorded`` is every date in the range, not the dates on a page.** The
    argument is a bare set of dates rather than a list of days precisely so
    that the difference is visible at the call site: handing this one page of a
    paged read reports every recorded day *after* that page as a day the
    athlete said nothing on, which is the opposite of true and reads as
    silence. `app.services.wellness.WellnessService.range` therefore computes
    it from a range-scoped query and hands the answer to the adapter, so an
    adapter has nothing to get wrong.
    """
    seen = set(recorded)
    span = (end - start).days
    return tuple(
        start + dt.timedelta(days=offset)
        for offset in range(max(span, 0))
        if start + dt.timedelta(days=offset) not in seen
    )
