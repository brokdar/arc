"""CSV cells a spreadsheet cannot execute, in either of the two CSV dialects.

A spreadsheet opening a CSV treats a cell that starts with ``=``, ``+``,
``-`` or ``@`` as a formula, and a leading tab or carriage return is how one
gets past a check that only looks at the first visible character. arc's
exports carry text the athlete typed and text other apps' files brought in,
so a **text** cell starting with any of the six is prefixed with ``'`` — the
OWASP recommendation, and what every spreadsheet displays as the cell's own
text.

A **number** is never escaped. Escaping by first character alone would turn a
legacy assistance weight of ``-5.0`` into the text ``'-5.0``, which no
spreadsheet can sum, so the decision is made on the Python type: ``str`` is
text, ``int``/``float``/``bool`` are values. That is why cells are typed all
the way to the writer rather than formatted to strings by the caller.

Two dialects (direction §11.2): comma with a decimal point, and semicolon with
a decimal comma for a German-locale Excel. Lines end in a bare line feed:
every reader arc's exports are for (Excel, LibreOffice, pandas, the importers
of Hevy and Strong) accepts it, and CRLF would put a carriage return into
every diff of a committed fixture.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from enum import StrEnum

#: The leading characters that make a text cell a formula (§11.2).
FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

type Cell = str | int | float | bool | None


class CsvVariant(StrEnum):
    """Which separators a CSV uses: the field delimiter and decimal mark."""

    COMMA = "comma"
    SEMICOLON = "semicolon"

    @property
    def delimiter(self) -> str:
        """The character between fields."""
        return "," if self is CsvVariant.COMMA else ";"

    @property
    def decimal(self) -> str:
        """The character inside a number with a fraction."""
        return "." if self is CsvVariant.COMMA else ","


def escape_text(value: str) -> str:
    """Prefix ``'`` to text a spreadsheet would otherwise run as a formula."""
    if value.startswith(FORMULA_TRIGGERS):
        return f"'{value}"
    return value


def format_cell(value: Cell, variant: CsvVariant) -> str:
    """One cell as written, before the writer's quoting.

    ``None`` is the empty field — unknown, never zero. A float is written by
    ``repr`` (shortest text that reads back as the same float, so ``100.0``
    stays ``100.0`` and ``102.5`` is never ``102.49999``), with the variant's
    decimal mark. ``bool`` is checked before ``int`` because it is one.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value).replace(".", variant.decimal)
    return escape_text(value)


def write_csv(
    header: Sequence[str], rows: Iterable[Sequence[Cell]], variant: CsvVariant
) -> str:
    """A whole CSV document: one header line, then one line per row.

    The header is written as given (arc's own column names never start with a
    trigger). Quoting is RFC 4180's, done by :mod:`csv`, so a note holding the
    delimiter, a quote or a newline comes back intact. No BOM.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=variant.delimiter, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([format_cell(cell, variant) for cell in row])
    return buffer.getvalue()
