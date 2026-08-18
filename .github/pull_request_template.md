<!--
The PR title must be a Conventional Commit — `feat(sessions): add session matching`.
`main` is squash-only, so this title becomes the commit subject on main and is
what the changelog is generated from. CI fails the PR if it does not parse.

Pick ONE of the two sections below and delete the other: "Why / What changed"
for a feature, "Root cause / The fix / Impact" for a bug fix.

There is NO line-length limit here. Write one line per paragraph, however long
— do not wrap at 72/76/80 columns. GitHub renders every newline inside a
paragraph as a line break, so wrapped prose comes out ragged, and it re-wraps
the text itself for the squash commit on main.

There is deliberately no test/validation checklist: `just check` and CI are the
gate, and a hand-ticked list is a claim no one verifies.
-->

## Why

<!-- Feature: the problem this solves and what we set out to build. -->

## What changed

<!-- Feature: what actually shipped. Name the route, setting, model, service or
     migration. Don't list files — the diff does that. -->

## Root cause

<!-- Bug: what was actually causing it. -->

## The fix

<!-- Bug: how it was fixed, and — where a future edit could reintroduce it
     silently — the test that now fails when it does. -->

## Impact

<!-- Bug: what happened while it was live. Which surface, whose data, how it
     showed up; say plainly if stored rows are wrong and whether anything
     backfills them. -->

## Notes

<!-- Decisions and tradeoffs, or where this deviated from or refined the issue.
     Delete the section if there is nothing real to say — don't pad it. -->

Closes #
