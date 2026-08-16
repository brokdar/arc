"""The vocabulary of a cloud connection: who it is with, and how it is doing.

Pure, like everything in this layer: the two enums are stored by
`app.persistence.connections`, published by `app.api.schemas.connections` and
branched on by the settings panel, so they have to be spelled once, in the one
place none of those three may disagree with.

:func:`normalise_remote_path` is here for the same reason. "Is this the folder
arc is already watching?" is a rule, not a database detail: Dropbox is
case-insensitive and answers with a `path_lower`, so `/Apps/WahooFitness/`,
`/apps/wahoofitness` and `/APPS/WAHOOFITNESS` are one folder, and a system that
stored them as three would poll the same directory three times and call it
three feeds.
"""

from enum import StrEnum


class ConnectionProvider(StrEnum):
    """The cloud services arc can hold a credential for.

    One member today. It exists as an enum rather than a constant because the
    tables, the routes and the panel are all provider-shaped already, and the
    alternative — a `connections` table that can only ever mean Dropbox —
    would have to be widened by a migration on the day a second one arrives.
    """

    DROPBOX = "dropbox"


class ConnectionStatus(StrEnum):
    """Whether arc can currently use the credential it is holding.

    Three states with three different remedies, which is why `error` is not
    folded into `needs_reauth`:

    * ``connected`` — the credential works, or at least nothing has told arc
      otherwise;
    * ``needs_reauth`` — Dropbox refused the refresh token. The athlete has to
      go through the connect ritual again; nothing local will fix it;
    * ``error`` — arc cannot *read* its own credential, which today means
      `SECRETS__ENCRYPTION_KEY` has changed since the row was written. The
      remedy is to restore the key, and re-authorizing would only paper over a
      configuration mistake that is also hiding every other secret.
    """

    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"
    ERROR = "error"


def normalise_remote_path(path: str) -> str:
    """Reduce a remote folder path to the one spelling arc stores.

    Lowercased (Dropbox is case-insensitive and reports `path_lower`), stripped
    of surrounding whitespace and of any trailing slash, and given a leading
    slash if it is missing. The Dropbox **root** is the empty string — that is
    Dropbox's own spelling for it in `list_folder`, and `"/"` normalises to it
    rather than to a path that would be rejected upstream.

    Returns:
        The stored form: ``""`` for the root, otherwise ``/lower/case/path``.
    """
    trimmed = path.strip().rstrip("/")
    if not trimmed:
        return ""
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    return trimmed.lower()
