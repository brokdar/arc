"""Guard: the `arc` hypothesis profile is registered and loaded, with no deadline.

Asserting only `settings().deadline is None` would have been green in CI with
the registration deleted — hypothesis loads its own `ci` profile when
`is_in_ci()`, and that profile has no deadline either, so the guard would go on
passing on the one machine that gates merges while the local suite quietly got
its 200 ms back. So both halves are named: that `arc` is the profile in force,
and that `arc` is what carries the setting. See the `tests/conftest.py`
docstring for why a deadline is a timing assertion this suite does not intend.
"""

from hypothesis import settings


def test_the_arc_profile_is_the_one_in_force() -> None:
    assert settings.get_current_profile_name() == "arc"


def test_the_arc_profile_runs_without_a_deadline() -> None:
    assert settings.get_profile("arc").deadline is None
