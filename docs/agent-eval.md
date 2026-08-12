# Agent evaluation protocol for the MCP coaching surface

This document defines how we measure the quality of arc's MCP surface as an
interface *for a coaching agent*, and records the baseline measured on
2026-08-11. It is the instrument we use to prove — not assert — that the
agent surface actually improved after a round of fixes.

## 1. Purpose

Interface quality for an agent is not something you can review statically;
you measure it by running a realistic coaching task against a fresh instance
and counting the friction. This follows Anthropic's tool-evaluation
methodology: define task-level evals that mirror real use, run them
end-to-end, and then *read the transcripts* — the interesting signal is where
the agent retried, guessed, or worked around the interface, not just whether
the task eventually passed. A tool surface can be "correct" and still burn
twenty calls teaching itself a schema the interface should have handed over
in one. The external coaching-agent review of 2026-08-11 (claude-opus-5,
connected over MCP only) is iteration zero of this eval: it executed the task
below, and its friction counts set the baseline in section 3. Its findings
were filed as issues #17–#26.

## 2. The eval task (fixed, repeatable)

The task is deliberately fixed so that runs are comparable across interface
changes. Hold the model, the system prompt, and the athlete material as
constant as practical between runs; what varies is the MCP surface under
test.

**Setup.** A fresh arc instance with roughly 14 ingested cycling sessions
and an empty athlete profile. The agent connects via MCP only — no repo
access, no REST API, no database. Whatever the agent cannot reach through
the MCP tools, it cannot use; that constraint is the point.

**The agent must:**

1. **Record anchors.** An estimated FTP with a confidence interval and a
   protocol/derivation text longer than 200 characters, a max HR, and a
   resting HR. Deliberately leave LTHR empty — it is unknown, and a good
   agent should record absence rather than invent a value.
2. **Author 8 workouts.** Six cycling workouts — Z2 endurance ×2, a long
   ride, a recovery ride, an EF reference ride, and an FTP test — plus two
   strength workouts (full body, upper body) built from the exercise
   catalogue.
3. **Propose a plan** for the remainder of the current week (3 sessions),
   with a rationale and an expiry.
4. **Write evaluations.** Three session evaluations and one week annotation.
5. **Simulate the NEXT coaching session.** Open with full context — athlete,
   anchors, current week, recent sessions — then determine the status of the
   proposal filed in step 3 (accepted, rejected, or lapsed), and check
   whether the historical sessions carry training load.

Step 5 is what makes this a *coaching-loop* eval rather than a data-entry
eval: a coach that can write but cannot later see the consequences of its
writes is flying blind, and the baseline showed exactly that.

## 3. Metrics and the 2026-08-11 baseline

Each run is scored on the metrics below. Counts come from the transcript;
"calls" means MCP tool invocations, including dry runs and failed attempts.

| Metric | Baseline (2026-08-11) | Target |
| --- | --- | --- |
| Calls to first valid workout structure | ~20 dry-run round trips (schema reverse-engineered field by field) | ≤2 (read example/purposes, then create) |
| Wasted purpose guesses | 4 (error did not enumerate the 16 valid values) | 0 |
| Calls to open a coaching session with full context | 4–6 | 1 |
| "Was my proposal accepted/rejected/lapsed?" answerable via MCP | no (write-then-blind) | yes, 1 call |
| Out-of-band reads (outside MCP) | 2 (exercise catalogue from the GitHub repo; zones hand-maintained in a local file) | 0 |
| Write crashes on valid input | 2 (`append_anchor` with protocol >200 chars; dry run had passed) | 0 |
| Historical sessions priced after first FTP anchor | 0 of 14 (permanently unpriced, silent) | 14 of 14 |
| Strength authorable via MCP alone | no | yes |
| Write budget visible while rationing writes | no (rationed blind) | yes (`budget_remaining` on write responses) |

Notes on reading the table:

- **Calls to first valid workout structure** counts every tool call from the
  first attempt at a structured workout to the first accepted one. The
  baseline agent had to discover the structure schema by submitting guesses
  as dry runs and parsing validation errors one field at a time (issue #21).
- **Write crashes on valid input** are the worst class: the dry run said yes
  and the real write returned a server error (issue #17). Any run with a
  nonzero count here fails regardless of the other numbers.
- **Historical sessions priced** measures whether appending the first FTP
  anchor retroactively gives earlier sessions a training load (issue #18).
  The baseline failure was silent — nothing told the agent the sessions
  would stay unpriced, which is why step 5 of the task checks it explicitly.
- **Out-of-band reads** count any information the agent needed for the task
  but had to obtain outside MCP. The baseline agent fetched the exercise
  catalogue from the GitHub repository and kept the athlete's zones in a
  hand-maintained local file (issue #20). The target is zero: the surface
  should be self-sufficient for its own task.

## 4. Transcript-reading checklist

The counts are necessary but not sufficient. After each run, read the full
transcript with these questions in hand:

- **Retries and guesses.** Where did the agent call the same tool more than
  once to get one thing done? Where did it guess a value (an enum, an ID, a
  unit) instead of reading it? Each guess marks a spot where the interface
  withheld something it knew.
- **Hedging and avoidance.** Did the agent qualify a statement it should
  have been able to verify ("the proposal was *probably* accepted")? Did it
  route around a tool entirely — doing arithmetic itself, keeping local
  notes — because the tool was unreliable or unreadable?
- **Docstring sentences that changed behaviour.** The baseline review
  explicitly credits two docstring instructions with steering the agent
  correctly: "never guess an anchor" and "search before create". These are
  behaviours to *protect* — any rewording of those docstrings must be
  re-checked against a run, not assumed harmless.
- **Over-claiming.** Did any tool response lead the agent to tell the
  athlete something untrue or unverified? A response that reads as
  confirmation when it is only acknowledgement (accepted vs. merely
  received) is an interface bug even if no metric catches it.
- **Tone of failure.** When a call failed, did the error message move the
  agent toward the fix (name the valid values, point at the right tool), or
  just report that something was wrong? Compare against issue #19.

## 5. Cadence and results

Re-run this eval after each substantial change to the MCP surface — new
tools, changed schemas, rewritten docstrings, altered error messages. Small
copy edits do not need a run, but anything that plausibly changes agent
behaviour does. Append each run's numbers to the results table below;
never rewrite earlier rows.

The full review artifact from iteration zero is the reference for what a
transcript reading should look like:

- Review artifact (iteration zero):
  <https://claude.ai/code/artifact/6ec1c525-03c6-4700-806b-357ce33063ec>
- Filed findings: issues #17–#26, milestone "Close the coaching loop".

### Results

| Date | Model | Calls to first valid workout | Wasted purpose guesses | Calls to open session | Proposal status via MCP | Out-of-band reads | Write crashes | Sessions priced | Strength via MCP | Budget visible | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08-11 | claude-opus-5 | ~20 | 4 | 4–6 | no | 2 | 2 | 0/14 | no | no | Iteration zero; external review, issues #17–#26 |
