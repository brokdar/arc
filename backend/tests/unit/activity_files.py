"""Hand-written GPX and TCX documents for the parser and pipeline tests.

FIT is binary and has its own generator (`golden_fit`); GPX and TCX are XML,
so the honest fixture is the document itself — a reader can see exactly what
the parser was given. Both builders take the same arguments so a test can ask
the same question of both formats.
"""

import datetime as dt
from collections.abc import Iterable

#: Where a track starts, so every document here lands on one known day.
START = dt.datetime(2026, 6, 1, 6, 0, tzinfo=dt.UTC)


def _stamp(moment: dt.datetime) -> str:
    """The ISO instant both formats write."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def gpx_document(
    *,
    start: dt.datetime = START,
    seconds: Iterable[int] = range(0, 600, 5),
    sport: str = "cycling",
    heart_rate: int = 140,
    power: int = 210,
) -> str:
    """A GPX track with Garmin's TrackPointExtension sensors on every point."""
    points = [
        f"""    <trkpt lat="{47.3769 + second * 2e-5:.6f}" lon="{8.5417 + second * 1.5e-5:.6f}">
      <ele>{412 + second * 0.1:.1f}</ele>
      <time>{_stamp(start + dt.timedelta(seconds=second))}</time>
      <extensions>
        <gpxtpx:TrackPointExtension>
          <gpxtpx:hr>{heart_rate}</gpxtpx:hr>
          <gpxtpx:cad>90</gpxtpx:cad>
          <gpxtpx:atemp>17</gpxtpx:atemp>
        </gpxtpx:TrackPointExtension>
        <power>{power}</power>
      </extensions>
    </trkpt>"""
        for second in seconds
    ]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="arc-tests"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk>
    <name>Test ride</name>
    <type>{sport}</type>
    <trkseg>
{chr(10).join(points)}
    </trkseg>
  </trk>
</gpx>
"""


def tcx_document(
    *,
    start: dt.datetime = START,
    seconds: Iterable[int] = range(0, 600, 5),
    sport: str = "Biking",
    heart_rate: int = 141,
    power: int = 212,
) -> str:
    """A TCX activity with one lap and the Garmin TPX speed/power extension."""
    offsets = list(seconds)
    points = [
        f"""        <Trackpoint>
          <Time>{_stamp(start + dt.timedelta(seconds=second))}</Time>
          <Position>
            <LatitudeDegrees>{47.3769 + second * 2e-5:.6f}</LatitudeDegrees>
            <LongitudeDegrees>{8.5417 + second * 1.5e-5:.6f}</LongitudeDegrees>
          </Position>
          <AltitudeMeters>{412 + second * 0.1:.1f}</AltitudeMeters>
          <HeartRateBpm><Value>{heart_rate}</Value></HeartRateBpm>
          <Cadence>91</Cadence>
          <Extensions>
            <TPX xmlns="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
              <Speed>8.4</Speed>
              <Watts>{power}</Watts>
            </TPX>
          </Extensions>
        </Trackpoint>"""
        for second in offsets
    ]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="{sport}">
      <Id>{_stamp(start)}</Id>
      <Lap StartTime="{_stamp(start)}">
        <TotalTimeSeconds>{offsets[-1] if offsets else 0}</TotalTimeSeconds>
        <DistanceMeters>2500</DistanceMeters>
        <Track>
{chr(10).join(points)}
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""
