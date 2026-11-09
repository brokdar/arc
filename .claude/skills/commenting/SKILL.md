---
name: commenting
description: >
  How to decide whether an inline code comment is worth writing, and what
  belongs in one. Covers implementation comments only — docstrings and
  public API documentation are a different artifact and out of scope. Use
  whenever you are about to write or edit a comment — while implementing or
  modifying code in any language, when asked to "add comments" or "clean up
  comments", and when reviewing or refactoring code whose comments restate
  the obvious. Also use it to decide that a comment should NOT be written.
---

# Commenting

A comment is written once but read many times, by a reader who has none of
the context you have right now: not the task description, not the diff, not
the conversation that led to this change, not the alternatives you tried and
discarded. The code itself will tell that reader *what* it does. The only
thing a comment can add is what the code **cannot** say — and every comment
that says something the code already says is noise the reader must parse,
distrust, and mentally discard.

**Scope: inline implementation comments only.** Docstrings and public API
descriptions serve a different reader — a caller who won't read the body —
so summarizing behavior there is the job, not noise. This skill neither
governs them nor argues for deleting them; follow their own conventions.

## The test, before writing any comment

Ask: **could a competent reader derive this from the code and its
surroundings alone?**

- If yes — don't write the comment. No comment is the correct default.
- If no — write it, and make it say the underivable thing, not the code's
  behavior.

"Competent reader" means someone who knows the language and can read the
function names, types, and tests — not someone seeing code for the first
time. Write for them, not for a beginner, and not for the person reviewing
today's diff.

## What earns a comment

**Why this way, not the obvious way.** When the code does something a reader
would be tempted to "fix" — an unusual algorithm, a check that looks
redundant, an order of operations that matters — say what the obvious
alternative was and why it loses. This is the comment that stops the next
person from reintroducing the bug you just avoided.

```python
# Sort before deduplicating: dict.fromkeys keeps first-seen order, and
# callers rely on results being alphabetical, not insertion-ordered.
names = list(dict.fromkeys(sorted(names)))
```

**Decisions that are non-obvious or non-standard.** A magic number's
provenance, a deliberate deviation from a convention, an invariant the code
assumes but cannot check. If a value or structure looks arbitrary but isn't,
record where it came from.

```go
// 48 bytes: the largest payload observed in production plus headroom;
// anything bigger indicates a corrupt frame, not a large message.
const maxFrameSize = 48
```

**Traps and constraints the code can't express.** Thread-safety assumptions,
ordering requirements between distant call sites, "this must stay in sync
with X" — anything where a locally-correct edit is globally wrong.

**Links to specific sources.** A workaround for an upstream bug, a behavior
mandated by a spec, a fix found in an issue thread — link the exact ticket,
commit, or doc section. The link is the load-bearing part: it lets the
reader check whether the workaround is still needed.

```ts
// Safari < 17 fires `visibilitychange` twice on bfcache restore; debounce
// rather than assume one event. https://bugs.webkit.org/show_bug.cgi?id=251345
```

## What never earns a comment

- **Narration** — the code says what it does more reliably than a comment
  ever will.
- **Change tracking** (dates, "was previously...", commented-out old code) —
  version control holds the history; a comment describing the *change*
  instead of the *code* is stale the moment it merges.
- **Talking to the reviewer** ("fixes the bug", "moved this from utils") —
  the future reader has no diff; to them these are riddles.
- **Restating the signature** — say something the name and types don't
  (units, error behavior, an invariant), or say nothing.
- **Section banners inside a function** — a function that needs signposts
  needs extracting; the extracted name is a signpost that can't drift.

## Prefer code that needs no comment

Before writing a comment that explains *what* confusing code does, try to
make the code not confusing: rename the variable, extract the expression
into a well-named function, split the condition. A "what" comment is a
refactoring that hasn't happened yet — the name travels with every call
site, while the comment sits in one place going stale. Reserve comments for
*why*, which no identifier can carry.

## Maintenance

- Put the comment on the exact line or block it governs, so an edit to that
  code forces the editor to confront the comment.
- When editing code, treat its comments as part of the change: update the
  ones your edit invalidates, and delete the ones your edit makes
  derivable. A wrong comment is worse than none — readers trust comments
  precisely because they're rare.
- When asked to "add comments" to code, apply the same test — the honest
  result may be two comments and three renames, not a comment per block.
  Don't manufacture comments to look thorough.

**Rule of thumb: if the comment wouldn't add anything the code can't say,
don't write it at all.**
