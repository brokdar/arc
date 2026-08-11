"""Prescription documents the tests plan sessions and workouts with.

Three documents, chosen so that a test can say "make this harder" and "make
this a different discipline" without writing another structure inline:

* :data:`EASY_RIDE` and :data:`HARD_RIDE` are the same hour at different
  intensities, which is what the red-flag rule needs a pair for — same
  purpose, same duration, more load;
* :data:`LIFT` is the strength one, which is what the cross-discipline half of
  that rule needs.

Shared rather than copied because two suites plan against them now (WP-8's
proposals and its MCP tools), and a fixture that drifts between two files is a
test that agrees with itself and nothing else.
"""

from typing import Any

#: A one-hour steady ride at 60-70% FTP. Cheap to predict and easy to make
#: harder.
EASY_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 3_600,
            "targets": {
                "power": {
                    "kind": "percent_of_anchor",
                    "anchor_type": "ftp",
                    "pct_low": 0.6,
                    "pct_high": 0.7,
                }
            },
        }
    ],
}

#: The same hour at 95-105% FTP: same purpose, much more load.
HARD_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 3_600,
            "targets": {
                "power": {
                    "kind": "percent_of_anchor",
                    "anchor_type": "ftp",
                    "pct_low": 0.95,
                    "pct_high": 1.05,
                }
            },
        }
    ],
}

#: Five triples of back squat at 100 kg.
LIFT: dict[str, Any] = {
    "discipline": "strength",
    "groups": [
        {
            "items": [
                {
                    "exercise_id": "back_squat",
                    "sets": 5,
                    "reps": 3,
                    "load": {"kind": "kg", "value": 100},
                }
            ]
        }
    ],
}


#: The same hour prescribed in **watts** rather than as a percentage. What it
#: costs therefore depends entirely on which FTP version it is priced against
#: — 100 TSS at an FTP of 200, 25 at an FTP of 400 — which is what makes it
#: the document for testing the freeze rule's re-pin (WP-8 D185).
WATT_HOUR: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 3_600,
            "targets": {
                "power": {"kind": "absolute", "low": 200, "high": 200, "unit": "W"}
            },
        }
    ],
}


def unstructured(duration_s: int) -> dict[str, Any]:
    """A ride of ``duration_s`` with no power target at all.

    Nothing to predict on either side — no target means no TSS — so a pair of
    these is how "the guard has no load to compare" is written down.
    """
    return {
        "discipline": "cycling",
        "steps": [{"kind": "steady", "duration_s": duration_s, "targets": {}}],
    }


def bodyweight(sets: int) -> dict[str, Any]:
    """``sets`` sets of five push-ups: strength that weighs nothing.

    A bodyweight prescription has no volume load on either side of a change,
    so the sets are the only signal that says how much work it is.
    """
    return {
        "discipline": "strength",
        "groups": [
            {
                "items": [
                    {
                        "exercise_id": "push_up",
                        "sets": sets,
                        "reps": 5,
                        "load": {"kind": "bodyweight"},
                    }
                ]
            }
        ],
    }
