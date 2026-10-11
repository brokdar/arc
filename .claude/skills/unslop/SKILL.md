---
name: unslop
description: >
  Cut AI writing tells and put arc's real voice into anything an agent writes
  here — PR titles and descriptions, commit messages, CHANGELOG entries, code
  comments and docstrings, GitHub issues, and status reports back to the
  operator in chat. Use before sending any of those, or when asked to unslop
  a draft. Owns voice only — the `commit` and `commit-push-pr` skills own
  format (Conventional Commits, PR templates, line-wrapping) and this skill
  defers to them rather than restating their rules.
allowed-tools: Read, Edit, Grep, Glob, Bash(git log:*), Bash(git diff:*), Bash(gh pr list:*), Bash(gh pr view:*)
---

# Unslop

After a multi-PR run, the operator said this about the status report:

> I am completely lost with the report you provide. LEARN TO BE CLEAR ON WHAT
> IS ACHIEVED; WHAT'S DONE (MERGED); WHAT'S OPEN... Summarize it, short
> precise, easy to follow and understand.

Followed immediately by: *"not like your reports..."* Chat reports are one
surface this skill owns. The other surfaces — PR bodies, commit messages,
docstrings — already have a real voice on `main`: the merged commit history
below, written by the operator. The failure mode is the same one in both
places: burying the two or three facts a reader needs under narrative padding,
hedges, or self-congratulation instead of just stating them.

## The surfaces this governs

| Surface | What lands there | Format owned by | This skill owns |
|---|---|---|---|
| PR title | Commit subject on `main`, changelog entry | `commit-push-pr` (Conventional Commits, lowercase, no period) | Whether it's a claim about behaviour or a task label |
| PR description | Commit body on `main`, changelog draft source | `commit-push-pr` (Why/What changed/Notes templates, one line per paragraph) | Density and honesty of the prose inside those sections |
| Commit message | Local history, raw material for the PR body | `commit` (type/scope/subject grammar) | Same as PR title/description, at the commit-body scale |
| CHANGELOG entry | `CHANGELOG.md`, hand-curated from `just changelog` | Keep a Changelog structure (untouched) | The bold lead-in states a fact, not a category |
| Code comment / docstring | The code itself | — | CLAUDE.md's "decisions live where they bind" rule: these carry the real reasoning — what was chosen, what it displaced, why — so padding here is worse than useless; the next reader trusts it's load-bearing |
| GitHub issue | Tracked work, PR `Closes #N` target | — | State the defect or request as a fact, not ticket-speak |
| Chat status report | What the operator reads right now | — | Entirely this skill's — no other skill covers it; see below |

## The house voice, from the log

`git log --pretty='=== %s%n%b' -40` is the evidence, not a guess. Read it
before writing anything long — it sets the bar.

**Subjects are claims about behaviour, not task names.** Compare what shipped
against what a task-log would have called it:

| Actually merged | A task-log subject would have said |
|---|---|
| `fix(backend): one clock answers "what day is it"` (#64) | `fix: date handling bug` |
| `fix(backend): the anchor in force survives a clock that steps backwards` (#66) | `fix: anchor lookup race condition` |
| `feat(ingest): dropbox files land in arc without being uploaded` (#55) | `feat: add dropbox polling` |
| `fix(tooling): the api-schema check compares content, not git state` (#65) | `fix: api schema check bug` |
| `feat(wellness): baselines that state their own maturity` (#42) | `feat: add baseline maturity field` |

The left column says what is true after the change. The right column says
what task produced it. Write the left column.

**PR bodies are dense and causal, not summarized.** From #64's `## Root
cause`: *"`MATCHING__TIMEZONE` defaults to `UTC` and no test moved the
athlete off it, so a right clock and a wrong one gave the same answer."* That
sentence names the mechanism that hid the bug, not just the bug. A slop
version would say "this issue was not caught by existing tests" and stop —
true, and useless, because it doesn't say *why* the tests missed it.

**`## Notes` sections admit what didn't get fixed, or what deviated.** From
#55: *"That budget is spent only by a failure that blames the page... a
condition that suspends all progress and lifts on its own does not spend
it."* From #66: *"This is deliberately small and touches nothing else, so
#64 rebases over it rather than absorbing it."* Neither hides a scoping
decision behind "future work" — they name the boundary and why it's there.

## Where this repo's voice differs from generic AI-tell advice

Most "avoid AI tells" advice bans the em dash outright. This repo's own
merged commit bodies use it 269 times across the last 40, correctly, as the
default way to attach a clause that narrows or explains the one before it:

> `current_anchor`, which supplied that day from ambient state and had no
> caller in `app/`, is removed rather than left as a fifth clock waiting for
> a caller. (#64)

That's not decorative pausing — the clause after the dash is doing real
work, naming the actual state removed. Banning the em dash here would ban
the house style. What actually reads as AI-written is a dash used to stall
("the fix is elegant — and effective") where a period would lose nothing.
Keep the dash when the clause after it adds a fact; cut it when it doesn't.

Also unlike generic advice: this repo's prose is never first person in a PR,
commit, or docstring — check the log, there is no "I fixed" or "we added"
anywhere in it. It describes the change directly: "`current` now asks
`anchor_effective_on`," not "I changed `current` to ask." Reserve "I" for
the one surface where it's actually the operator's read of an agent talking
to them: chat status reports.

## Process

1. Read the draft once for content only: is every claim specific and true —
   real file, real symbol, real number — or could it be pasted into another
   project's PR unchanged? If the second, it says nothing about this change.
   Cut it or make it concrete.
2. Scan for the patterns below.
3. Rewrite. State the mechanism, not the feeling — "the coaching agent got
   last week's plan beside this week's wellness" (#64), not "there were
   inconsistencies in the data".
4. Self-audit: *what in this specific sentence would tag it as
   agent-written rather than something the operator typed by hand?* Fix that
   sentence, don't soften the whole paragraph.

## Patterns that actually show up on arc's surfaces

Pruned to what appears here — a list of tells nobody on this repo would ever
write is padding in its own right.

- **AI vocabulary.** `leverage`, `delve`, `crucial`, `seamless`, `robust`,
  `streamline`, `comprehensive`, `foster`, `utilize`, `ensure`, `showcase`,
  `testament`, `underscore`. "Leverages the scheduler" becomes "runs on the
  scheduler." "Ensures consistency" becomes the actual invariant, or the
  test name that checks it.
- **Fancy ways to say "is".** "Serves as", "stands as", "boasts", "features".
  Just "is" or "has".
- **Superficial -ing phrases.** "Ensuring...", "enabling...", "providing
  seamless...". Delete, or name the real mechanism the way #64 does:
  "`GET /api/v1/clock` serves the zone and the day on it", not "ensuring the
  frontend has access to timezone data".
- **"Not just X, but Y."** State the point once.
- **Rule of three.** If there are two changes, or four, say two or four.
- **One name per thing.** CLAUDE.md's own convention: don't call the same
  entity "the clock" then "the timestamp" then "the value" across one PR
  body. Pick the name and repeat it.
- **Puffery in status reports.** "Great progress", "solid foundation", "in
  good shape". State what's merged, open, or blocked — see below.
- **Filler.** "In order to" becomes "to". "It is important to note that"
  gets deleted outright. "Due to the fact that" becomes "because".
- **Excessive hedging.** "Could potentially possibly be an issue" becomes
  the fact, or gets left out.
- **Chatbot phrases.** "Let me know if you have questions!", "I hope this
  helps!", "Great question!", "Certainly!" — none of these appear anywhere
  in 60 commits of this repo's history. Don't be the first.
- **Emoji as decoration.** None appear in the log except one describing a
  real shipped UI control (a 👍/👎 dispute button, #17–#26) — that's a
  product detail, not a tell. Decorative emoji in a heading or bullet is.
- **A "Testing" or "How to validate" section.** `commit-push-pr` already
  forbids this in PR bodies — `just check` and CI are the gate, a hand-ticked
  list is a claim nobody verified. Watch for the reflex to add one anyway.
- **A `Co-Authored-By:` or AI-attribution trailer.** Forbidden by CLAUDE.md.
  Don't add one out of habit from another project's conventions.

## Reporting run status back to the operator

The highest-value rule in this skill, because it's the one the operator
actually complained about. Order, every time:

1. **Merged** — done, on `main`, nothing left to do.
2. **Open** — exists, not yet merged, with the blocker named in the same
   line if there is one.
3. **Blocked** — can't proceed without something, and what that something is.
4. **Your move** — the operator's next action, one line, or "nothing needed
   from you" stated explicitly rather than left implied.

No narrative preamble. No "Great progress! Here's where things stand." Lead
with the list.

**Worked example**, pulled live from this repo (`gh pr list`, `gh pr view`)
while writing this skill, not invented:

```
Merged:
- #69 — integrations model unifies dropbox and every future source

Open:
- #67 — arc finds the folder the fit files are already in (conflicts with main, needs rebase)
- #68 — connect dropbox without editing .env or restarting (conflicts with main, needs rebase)

Your move: rebase #67 and #68 onto main, then they're ready for review.
```

Three lines of status, one line of instruction. Not: "I made great progress
on the connectors feature! #69 landed successfully, unifying the way arc
handles integrations, which is an important step forward. #67 and #68 are
still open and may need some attention before they're ready to merge."
Same facts, four times the words, and the one thing the operator needs — a
rebase, on two named PRs — is buried in "may need some attention" instead of
stated.

A blocker goes in a few words on its own line ("needs rebase", "waiting on
your review", "CI red on the migration check"), not in a paragraph of
explanation underneath. If there's a real reason worth a sentence, it comes
after the four-line status, never before it.

## Add voice

Removing tells is half the job; sterile writing left in their place is just
as obviously not-human.

- **Have opinions where the surface allows it.** A PR's `## Notes` and a
  chat report both have room for a real read — "#67 and #68 will conflict
  with each other until one merges", not a neutral list.
- **Vary rhythm**, the way the actual log does: #64's root cause mixes short
  declaratives ("Nothing detected this because nothing could.") with long
  sentences that carry a full causal chain.
- **Be specific instead of sterile.** Not "this caused issues" but "handed
  the coaching agent last week's plan beside this week's wellness" (#64).
- **Use "I" in chat.** It's the one surface where the agent is actually
  talking to the operator, not writing prose that lands in the repo.

## Self-audit

Before sending: *would this sentence survive someone asking "says what,
exactly"?* If the answer is a real file, function, number, or PR, it stays.
If the honest answer is "it's just saying it's done", cut it or replace it
with the fact the reader actually needs.
