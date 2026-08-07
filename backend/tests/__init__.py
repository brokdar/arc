"""The backend test suite, as a real package — deliberately.

These ``__init__.py`` files are load-bearing, not clutter. `garmin-fit-sdk`
(WP-4) ships its own **top-level** ``tests`` package inside its wheel, so
site-packages contains a regular package called ``tests``. A namespace package
loses to a regular one wherever it sits on ``sys.path``, so without these
files ``from tests.unit.conftest import ...`` resolves to the Garmin SDK's
test suite and every module here fails to import.

With them, ours is a regular package too, and pytest's prepend import mode
puts ``backend/`` at the front of ``sys.path`` — so ours wins. Do not remove
them while that dependency is installed.
"""
