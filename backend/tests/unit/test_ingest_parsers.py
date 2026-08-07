"""GPX and TCX parsing, the FIT fallback, and what "unreadable" means.

The FIT snapshots live in `test_golden_fit_files`; what is left is the two
text formats, the dispatcher, and the several distinct ways a file can be
refused — each of which the pipeline turns into a quarantine record with a
different sentence in it, so each is worth pinning.
"""

import datetime as dt
from pathlib import Path

import pytest

from app.domain.streams import (
    ParsedActivity,
    StreamChannel,
    channels_present,
    resample,
    validate,
)
from app.ingest.parsers import UnreadableFileError, extension_of, parse
from app.ingest.parsers import fit as fit_parser
from app.ingest.parsers.base import (
    NO_DEVICE_INFO,
    ONLY_CANDIDATE,
    choose_source,
)
from tests.unit.activity_files import START, gpx_document, tcx_document
from tests.unit.golden_fit import OUTDOOR_START, golden

#: Records the whole outdoor ride carries — the denominator a truncated
#: recovery is measured against.
OUTDOOR_SAMPLES = 902


def write(directory: Path, name: str, content: str) -> Path:
    """Write one document and return its path."""
    path = directory / name
    path.write_text(content)
    return path


def test_a_gpx_track_parses_to_one_activity_with_its_extensions(
    tmp_path: Path,
) -> None:
    [activity] = parse(write(tmp_path, "ride.gpx", gpx_document()))

    assert activity.file_sport_index == 0
    assert activity.sport == "cycling"
    assert activity.start_time == START
    # GPX carries no offset at all, so there is nothing better than UTC.
    assert activity.local_offset is None
    assert channels_present(activity.samples) == {
        StreamChannel.LAT,
        StreamChannel.LON,
        StreamChannel.ELEVATION,
        StreamChannel.HR,
        StreamChannel.CADENCE,
        StreamChannel.TEMP,
        StreamChannel.POWER,
    }
    assert activity.samples[0].values[StreamChannel.POWER] == 210.0
    # No device_info in GPX, so the source is the record field itself and the
    # rule says so rather than naming a meter nobody wrote down.
    assert activity.power_source_rule == NO_DEVICE_INFO


def test_a_tcx_activity_parses_with_its_laps_and_tpx_extension(
    tmp_path: Path,
) -> None:
    [activity] = parse(write(tmp_path, "ride.tcx", tcx_document()))

    assert activity.sport == "Biking"
    assert len(activity.laps) == 1
    assert channels_present(activity.samples) >= {
        StreamChannel.SPEED,
        StreamChannel.POWER,
        StreamChannel.HR,
    }
    assert activity.samples[0].values[StreamChannel.SPEED] == 8.4
    assert resample(activity.samples).elapsed_time_s == 595.0


def test_a_tcx_without_gps_keeps_its_indoor_trackpoints(tmp_path: Path) -> None:
    # `tcxreader` drops leading and trailing GPS-less points by default, which
    # would truncate every indoor session. The parser overrides that.
    document = tcx_document().replace(
        "<Position>\n            <LatitudeDegrees>47.376900</LatitudeDegrees>\n"
        "            <LongitudeDegrees>8.541700</LongitudeDegrees>\n"
        "          </Position>\n          ",
        "",
    )

    [activity] = parse(write(tmp_path, "indoor.tcx", document))

    assert activity.start_time == START, "the first point is still the start"


def test_an_unknown_extension_is_refused_by_name(tmp_path: Path) -> None:
    path = write(tmp_path, "ride.csv", "time,power\n")

    with pytest.raises(UnreadableFileError, match="not a file type"):
        parse(path)


def test_a_corrupt_fit_file_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "broken.fit"
    path.write_bytes(b"\x00\x01\x02not a fit file at all\xff" * 10)

    with pytest.raises(UnreadableFileError, match="not a readable FIT"):
        parse(path)


@pytest.mark.parametrize("fraction", [0.30, 0.50, 0.60, 0.80, 0.95])
def test_a_truncated_fit_file_keeps_the_ride_written_before_the_cut(
    tmp_path: Path, fraction: float
) -> None:
    # The flat battery: the whole reason the tolerant reader exists. A real
    # golden file cut mid-record is refused outright by the strict SDK, while
    # `fitdecode` hands over every frame ahead of the cut before it raises —
    # and those frames are the ride the athlete most wants back. Cutting a
    # *real* prefix rather than a header-sized stub is what makes the recovery
    # path reachable at all.
    whole = golden("outdoor_ride.fit").read_bytes()
    path = tmp_path / "truncated.fit"
    path.write_bytes(whole[: int(len(whole) * fraction)])

    [activity] = parse(path)

    recovered = len(activity.samples) / OUTDOOR_SAMPLES
    assert recovered == pytest.approx(fraction, abs=0.05), (
        "the recovery should be proportional to how much of the file survived"
    )
    assert activity.samples[0].t == OUTDOOR_START, "recovery starts at the start"
    # FIT writes `session`, `lap` and `activity` at the *end* of the file, so a
    # recovered ride honestly has no sport, no laps and no local offset rather
    # than borrowed ones.
    assert (activity.sport, activity.laps, activity.local_offset) == (None, (), None)
    # The parser returns what it got; whether that is enough to ingest is
    # `validate`'s call, and for these fractions the answer is yes.
    assert validate(activity) is None


def test_a_fit_file_that_is_only_a_header_is_unreadable(tmp_path: Path) -> None:
    # The other edge of the same behaviour: twenty bytes carry no frame at all,
    # and an empty harvest must be a quarantine — never a session built from a
    # header alone.
    whole = golden("outdoor_ride.fit").read_bytes()
    path = tmp_path / "header_only.fit"
    path.write_bytes(whole[:20])

    with pytest.raises(UnreadableFileError, match="no samples could be decoded"):
        parse(path)


def test_a_file_the_strict_decoder_reads_never_reaches_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fallback is for files the SDK refuses. Reaching it for a clean file
    # would silently swap the decoder that resolves the profile's enums for the
    # one that does not.
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("the tolerant reader ran for a file the SDK read")

    monkeypatch.setattr(fit_parser, "_read_with_fitdecode", refuse)

    [activity] = parse(golden("outdoor_ride.fit"))

    assert len(activity.samples) == OUTDOOR_SAMPLES


def provenance(activity: ParsedActivity) -> dict[str, object]:
    """Everything an activity claims about where its numbers came from."""
    return {
        "power_source_candidates": list(activity.power_source_candidates),
        "power_source": activity.power_source,
        "power_source_rule": activity.power_source_rule,
        "hr_source_candidates": list(activity.hr_source_candidates),
        "hr_source": activity.hr_source,
        "hr_source_rule": activity.hr_source_rule,
    }


@pytest.mark.parametrize(
    ("name", "hr_candidates"),
    [
        ("outdoor_ride.fit", ["garmin/1234 #2"]),
        ("indoor_trainer.fit", ["garmin/1234 #3"]),
        ("strength_watch.fit", ["garmin/1234 #1"]),
        ("brick.fit", []),
    ],
)
def test_both_fit_decoders_read_the_same_provenance_from_the_same_bytes(
    name: str, hr_candidates: list[str]
) -> None:
    # The module docstring promises the two decoders "cannot drift into
    # producing different sessions from the same bytes"; this is the pin that
    # makes it true. It reaches for the private readers because the property
    # under test *is* that the pair agree, and nothing public can name both.
    #
    # The concrete case: FIT keeps a Garmin sensor's product number in the
    # `garmin_product` subfield, which the SDK reports alongside `product` and
    # `fitdecode` reports instead of it — so reading `product` first labelled
    # the same strap `garmin/1234` or `garmin/unknown` depending on which
    # decoder happened to run.
    path = golden(name)

    strict = fit_parser._activities(fit_parser._read_with_sdk(path))  # noqa: SLF001
    tolerant = fit_parser._activities(  # noqa: SLF001
        fit_parser._read_with_fitdecode(path, sdk_error=RuntimeError("forced"))
    )

    assert [provenance(activity) for activity in strict] == [
        provenance(activity) for activity in tolerant
    ]
    assert list(strict[0].hr_source_candidates) == hr_candidates


def test_a_gpx_route_without_times_is_not_a_recording(tmp_path: Path) -> None:
    document = gpx_document()
    stripped = "\n".join(line for line in document.splitlines() if "<time>" not in line)

    with pytest.raises(UnreadableFileError, match="not a recording"):
        parse(write(tmp_path, "route.gpx", stripped))


def test_a_gpx_that_is_not_xml_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(UnreadableFileError, match="not readable GPX"):
        parse(write(tmp_path, "ride.gpx", "this is not xml"))


def test_extensions_are_normalised_for_dispatch_and_storage() -> None:
    assert extension_of(Path("/data/inbox/Ride.FIT")) == "fit"
    assert extension_of(Path("noextension")) == "bin"


def test_a_channel_with_no_samples_gets_no_source() -> None:
    # A file that names a power meter but records no power must not claim a
    # power source: the recording row would advertise a column it lacks.
    candidates, source, rule = choose_source(
        ["srm/7 #1"], channel=StreamChannel.POWER, present=False
    )

    assert (candidates, source, rule) == ((), None, None)


def test_a_single_candidate_records_the_rule_that_there_was_no_choice() -> None:
    candidates, source, rule = choose_source(
        ["srm/7 #1"], channel=StreamChannel.POWER, present=True
    )

    assert (candidates, source, rule) == (("srm/7 #1",), "srm/7 #1", ONLY_CANDIDATE)


def test_gpx_timestamps_come_back_as_aware_utc(tmp_path: Path) -> None:
    [activity] = parse(write(tmp_path, "ride.gpx", gpx_document()))

    assert all(sample.t.tzinfo is dt.UTC for sample in activity.samples)
