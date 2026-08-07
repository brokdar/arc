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

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from app.domain.streams import ParsedActivity
from app.ingest.parsers.base import SUPPORTED_EXTENSIONS, UnreadableFileError
from app.ingest.parsers.fit import parse_fit
from app.ingest.parsers.gpx import parse_gpx
from app.ingest.parsers.tcx import parse_tcx

__all__ = [
    "FALLBACK_EXTENSION",
    "MAX_EXTENSION_LENGTH",
    "SUPPORTED_EXTENSIONS",
    "UnreadableFileError",
    "extension_of",
    "parse",
]

#: Lowercase extension (no dot) -> the parser that reads it.
PARSERS: Mapping[str, Callable[[Path], Sequence[ParsedActivity]]] = {
    "fit": parse_fit,
    "gpx": parse_gpx,
    "tcx": parse_tcx,
}


#: Longest extension kept. The ``recordings.original_ext`` column is
#: ``String(16)``, and every extension this application reads is three
#: characters; anything past this is not a file type, it is a payload.
MAX_EXTENSION_LENGTH = 15

#: Used when a name carries no extension, or one that is not one.
FALLBACK_EXTENSION = "bin"

#: The shape of an extension we will write to disk: lowercase alphanumerics,
#: bounded. Not a sanitiser — a *test*, because an extension that needs
#: sanitising is not the file's type in any useful sense.
_EXTENSION = re.compile(rf"^[a-z0-9]{{1,{MAX_EXTENSION_LENGTH}}}$")


def extension_of(path: Path) -> str:
    """The file's extension, lowercase, bounded and safe to build a name from.

    Used for the stored ``original_ext``, for the name the original is filed
    under and for the quarantined copy's name, so it is normalised in exactly
    one place — and the normalisation is a **bound**, not a nicety: a
    200-character extension survives filename sanitising, and the
    ``<64 hex>.<ext>`` name built from it exceeds every filesystem's 255-byte
    limit. The resulting ``ENAMETOOLONG`` cannot be recovered from by moving
    the file somewhere else (the same name is used there), so the file stays in
    the inbox and every sweep from then on dies on it.

    Anything that is not one to fifteen lowercase alphanumerics — absent,
    overlong, or carrying separators — is reported as
    :data:`FALLBACK_EXTENSION`. Dispatch in :func:`parse` reads the same value,
    so such a file is refused as unreadable rather than filed as one type and
    parsed as another.
    """
    candidate = path.suffix.lstrip(".").lower()
    return candidate if _EXTENSION.match(candidate) else FALLBACK_EXTENSION


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
