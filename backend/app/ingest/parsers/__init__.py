"""File parsers: one function, one shape, three formats.

`parse` is the only entry point the pipeline uses. It returns a **sequence** of
`app.domain.streams.ParsedActivity` because one file may hold more than one
sport (A4.5) — a single-sport file simply yields one element, and nothing
downstream has to learn the difference later.

These modules are the only place `garmin-fit-sdk`, `fitdecode`, `gpxpy` and
`tcxreader` may be imported; the domain-purity contract in `pyproject.toml`
names all four, so an import of any of them from `app/domain` is a build
error. Nothing above this layer sees a FIT record: it sees samples and
dataclasses.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from app.domain.streams import ParsedActivity
from app.ingest.parsers.base import SUPPORTED_EXTENSIONS, UnreadableFileError
from app.ingest.parsers.fit import parse_fit
from app.ingest.parsers.gpx import parse_gpx
from app.ingest.parsers.tcx import parse_tcx

__all__ = ["SUPPORTED_EXTENSIONS", "UnreadableFileError", "extension_of", "parse"]

#: Lowercase extension (no dot) -> the parser that reads it.
PARSERS: Mapping[str, Callable[[Path], Sequence[ParsedActivity]]] = {
    "fit": parse_fit,
    "gpx": parse_gpx,
    "tcx": parse_tcx,
}


def extension_of(path: Path) -> str:
    """The file's extension, lowercase and without the dot.

    Used for the stored ``original_ext`` and for the name the original is
    filed under, so it is normalised in exactly one place.
    """
    return path.suffix.lstrip(".").lower()


def parse(path: Path) -> Sequence[ParsedActivity]:
    """Parse one device file into its activities.

    Args:
        path: The file to read. Dispatch is by extension — the formats are not
            sniffable cheaply and a head unit writes the right one.

    Returns:
        One :class:`~app.domain.streams.ParsedActivity` per sport in the file,
        in file order, each with its samples sorted by time.

    Raises:
        UnreadableFileError: When the extension is not one we read, or the
            file behind it is not a recording we can decode.
    """
    parser = PARSERS.get(extension_of(path))
    if parser is None:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UnreadableFileError(
            f"{path.name!r} is not a file type this application reads "
            f"(expected one of: {supported})"
        )
    return parser(path)
