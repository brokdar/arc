"""Spreadsheet-safe CSV cells: text that could run as a formula is defused.

A spreadsheet treats a cell beginning with ``=``, ``+``, ``-`` or ``@`` as a
formula, and a leading tab or carriage return as a way to smuggle one past a
naive check. arc exports notes the athlete — or an imported file — wrote, so
every *text* cell that starts with one of those is prefixed with ``'``. A
*number* never is: a legacy assistance weight of -5.0 is a value, and
quoting it would turn it into text no spreadsheet can sum.

The properties at the bottom state the rule for any input; the examples above
them are the acceptance criterion's own cases (AC-79).
"""

import csv
import io
import math

from hypothesis import given
from hypothesis import strategies as st

from app.domain.csv_safety import (
    FORMULA_TRIGGERS,
    CsvVariant,
    format_cell,
    write_csv,
)


def parsed(text: str, variant: CsvVariant) -> list[list[str]]:
    """Read a written CSV back the way a spreadsheet's importer would."""
    return list(csv.reader(io.StringIO(text), delimiter=variant.delimiter))


# --- AC-79: the criterion's own cases -----------------------------------------


def test_a_formula_note_is_escaped() -> None:
    """A set note "=SUM(A1)" exports as "'=SUM(A1)"."""
    text = write_csv(["set_notes"], [["=SUM(A1)"]], CsvVariant.COMMA)

    assert parsed(text, CsvVariant.COMMA) == [["set_notes"], ["'=SUM(A1)"]]


def test_a_negative_weight_is_written_as_a_number_unescaped() -> None:
    """A legacy assistance weight of -5.0 stays -5.0, never '-5.0."""
    text = write_csv(["weight_kg"], [[-5.0]], CsvVariant.COMMA)

    assert text == "weight_kg\n-5.0\n"


def test_the_semicolon_variant_writes_a_decimal_comma() -> None:
    """German Excel: semicolons between cells, a comma inside a number."""
    text = write_csv(["weight_kg", "reps"], [[102.5, 5]], CsvVariant.SEMICOLON)

    assert text == "weight_kg;reps\n102,5;5\n"
    assert parsed(text, CsvVariant.SEMICOLON) == [["weight_kg", "reps"], ["102,5", "5"]]


def test_the_semicolon_variant_does_not_escape_a_negative_number() -> None:
    """The decimal comma does not make a number text."""
    assert format_cell(-5.0, CsvVariant.SEMICOLON) == "-5,0"


def test_a_cell_starting_with_a_tab_is_escaped() -> None:
    """A leading tab is how a formula hides from a check on the first byte."""
    text = write_csv(["set_notes"], [["\t=1+1"]], CsvVariant.COMMA)

    assert parsed(text, CsvVariant.COMMA)[1] == ["'\t=1+1"]


def test_a_cell_starting_with_a_carriage_return_is_escaped() -> None:
    """So is a leading carriage return, which survives the CSV quoting."""
    text = write_csv(["set_notes"], [["\r=1+1"]], CsvVariant.COMMA)

    assert parsed(text, CsvVariant.COMMA)[1] == ["'\r=1+1"]


def test_an_empty_note_is_an_empty_field() -> None:
    """Nothing written, and no stray quote: empty means unknown."""
    text = write_csv(["reps", "set_notes", "rpe"], [[5, "", None]], CsvVariant.COMMA)

    assert text == "reps,set_notes,rpe\n5,,\n"


def test_every_trigger_is_escaped() -> None:
    """The six characters the direction names (§11.2), and only text."""
    assert set(FORMULA_TRIGGERS) == {"=", "+", "-", "@", "\t", "\r"}
    for trigger in FORMULA_TRIGGERS:
        assert format_cell(f"{trigger}x", CsvVariant.COMMA) == f"'{trigger}x"


def test_a_plain_word_and_a_mid_string_sign_are_untouched() -> None:
    """Only the first character decides: "felt +2kg" is a sentence."""
    assert format_cell("felt +2kg", CsvVariant.COMMA) == "felt +2kg"
    assert format_cell("Bench Press", CsvVariant.COMMA) == "Bench Press"


def test_booleans_and_integers_are_numbers() -> None:
    """A flag is written as true/false, a count as digits — never escaped."""
    assert format_cell(True, CsvVariant.COMMA) == "true"
    assert format_cell(False, CsvVariant.COMMA) == "false"
    assert format_cell(-3, CsvVariant.COMMA) == "-3"


# --- properties ---------------------------------------------------------------

finite = st.floats(allow_nan=False, allow_infinity=False)


@given(st.text())
def test_no_text_cell_reads_as_a_formula(value: str) -> None:
    """Whatever the text, the written cell never starts with a trigger."""
    cell = format_cell(value, CsvVariant.COMMA)

    assert not cell.startswith(FORMULA_TRIGGERS)
    assert cell in {value, f"'{value}"}


@given(st.text())
def test_text_survives_a_round_trip_through_the_writer(value: str) -> None:
    """Quoting is the writer's job: commas, quotes and newlines come back."""
    for variant in CsvVariant:
        rows = parsed(write_csv(["note"], [[value]], variant), variant)

        assert rows[1] == [format_cell(value, variant)]


@given(finite)
def test_a_number_is_never_escaped_and_reads_back_exactly(value: float) -> None:
    """Negative or not, a number is a number, and it is the same number."""
    for variant in CsvVariant:
        cell = format_cell(value, variant)

        assert not cell.startswith("'")
        back = float(cell.replace(variant.decimal, "."))
        assert back == value
        assert math.copysign(1, back) == math.copysign(1, value)
