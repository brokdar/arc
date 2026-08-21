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

    * ``connected`` — a scoped call to the provider succeeded, and
      ``connections.last_verified_at`` says when. **An observation with a
      timestamp, not the absence of bad news.** It used to mean "nothing has
      told arc otherwise", which is a claim arc could go on making for weeks
      after a console permission change killed the grant: nothing asks, so
      nothing tells. The status and the stamp are read together — a status
      with no stamp behind it is a connection nobody has checked yet, and the
      panel says exactly that rather than inventing a time;
    * ``needs_reauth`` — Dropbox refused the credential, or refused a call for
      want of a scope the grant does not carry. The athlete has to go through
      the connect ritual again (after ticking the permission, where that is
      what went wrong); nothing local will fix it;
    * ``error`` — arc cannot *read* its own credential, which today means
      `SECRETS__ENCRYPTION_KEY` has changed since the row was written. The
      remedy is to restore the key, and re-authorizing would only paper over a
      configuration mistake that is also hiding every other secret.
    """

    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"
    ERROR = "error"


#: The audit action a feed writes for each file it turned into a session.
#:
#: In the domain rather than beside the code that writes it (`app.ingest.feeds`)
#: because it is read from `app.services.connections`, which the layer contract
#: forbids from importing `app.ingest` — and a string duplicated across that
#: boundary is one that eventually stops matching, silently, leaving the coach
#: a delivery count of zero on a feed that is working perfectly.
FEED_DELIVERED_ACTION = "feed.delivered"


class FeedDeliveryState(StrEnum):
    """How a watched folder is doing, in one word the coach can branch on.

    The vocabulary exists because the numbers alone are ambiguous in exactly
    the case that matters: `last_delivery_at: null` and `deliveries_7d: 0` read
    identically for a folder that was connected an hour ago and for one whose
    credential died last Tuesday, and the second is a week of missing rides.

    * ``paused`` — the athlete switched this feed off. Silence is intended.
    * ``failing`` — the last poll left an error on the row. Silence is a fault.
    * ``never_delivered`` — nothing has ever arrived through it. **Never
      reported as ``0`` deliveries and never as an error**: a folder that has
      not delivered yet is a fact about setup, and both of the other spellings
      would send a coach looking for a break that is not there.
    * ``delivering`` — arc has heard from Dropbox and nothing is wrong. Whether
      any *rides* arrived is `deliveries_7d`, which is the training question;
      this is the plumbing question.

    Order matters where two apply: an athlete's own pause outranks everything,
    then a recorded fault, then the absence of a first delivery.
    """

    PAUSED = "paused"
    FAILING = "failing"
    NEVER_DELIVERED = "never_delivered"
    DELIVERING = "delivering"


#: The extensions arc treats as an activity file, without the dot.
#:
#: In the domain rather than beside the poll that acts on it
#: (`app.ingest.feeds`) for the reason :data:`FEED_DELIVERED_ACTION` is: it is
#: now read from `app.services.connections` too, which the layer contract
#: forbids from importing `app.ingest`. Folder discovery ranks a folder by how
#: many of these are in it, and the poll then downloads exactly those — two
#: copies of the list would eventually disagree, and the failure is silent:
#: discovery would recommend a folder the poll then ignores.
#:
#: It is also the only thing the poll will spend a download on. Everything else
#: is skipped *without* downloading it and **without an ingest event**: the
#: alternative — download everything and let the pipeline quarantine what it
#: cannot read — was rejected because a real Dropbox folder holds screenshots,
#: CSV exports and the odd PDF, and a quarantine queue full of files that were
#: never rides is a queue nobody reads. The queue's value is that everything in
#: it is a decision somebody has to take.
ACTIVITY_EXTENSIONS = frozenset({"fit", "gpx", "tcx"})


def is_activity_file(name: str) -> bool:
    """Whether a filename looks like a ride arc could read.

    Case-insensitive, because Dropbox stores the name the device wrote and a
    Garmin writes `RIDE.FIT` in capitals; a file with no extension at all is
    not one, because the dispatch that would eventually parse it has nothing
    to dispatch on.

    A name, not a path, and a *guess*, not a promise: this decides whether a
    folder is worth recommending, and the file is only read later.
    """
    _, dot, extension = name.rpartition(".")
    return bool(dot) and extension.lower() in ACTIVITY_EXTENSIONS


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
