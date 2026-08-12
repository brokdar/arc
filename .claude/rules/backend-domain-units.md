---
paths: backend/app/domain/**, backend/app/services/**, frontend/lib/**
---

# Units and ranges are one convention, system-wide

Two spellings that every layer shares. Both are invisible when broken — the
wrong value is a plausible number, not an error — so they are conventions
rather than checks, and a new module must match them without being asked.

1. **A percentage is a fraction.** `0.88` is 88 %, everywhere: workout targets
   (`PercentOfAnchor.pct_low`), ceiling limits (`PercentLimit.pct`), band
   tolerances (`Band.low` / `.high`), similarity components, coverage ratios.
   Nothing in the domain, the services or `frontend/lib` stores a percentage
   as `88`. One convention, or every consumer has to know which of two scales
   a given number is on — and the mistake does not announce itself, because
   `88` and `0.88` are both plausible-looking.

   The API renders percentages for display where a human reads them; that
   conversion happens at the edge, on the way out, and never travels back in.

2. **A percentage may only be taken of its own anchor.** A channel resolves
   against the anchor type that channel is measured in — power against FTP,
   heart rate against LTHR or max HR — and a cadence target expressed as a
   percentage of FTP is refused rather than computed. The failure this
   prevents is silent: 90 % of FTP applied to a heart rate is a number, just
   not anyone's threshold.

3. **Every range is half-open, `[start, end)`.** Recording stops, anomaly
   regions, detected work intervals, zone bands, chart selections, week
   windows. A range's length is `end - start` with no `+ 1` anywhere, and two
   adjacent ranges share a boundary index without overlapping on it. The one
   place this was written as `to - from + 1` counted a selection one second
   long as two, and agreed with nothing else on the page.

   Zone bands inherit it as `lower <= x < upper`, which is what makes time in
   zone total to the recording's duration rather than losing the values that
   fall in a published table's one-point gaps.
