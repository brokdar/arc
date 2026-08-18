# Publishing a spec: GitHub issue, with a docs/ fallback

This is the shared output procedure for `feature-specify` and `bug-report`. Both skills do
their discovery work first, fill in their template, and then come here to **publish** the result.

The intent: a spec is most useful where the team already works — as a tracked GitHub issue, not
as a markdown file that rots in the repo. So the default destination is a GitHub issue. Writing a
file locally is the *fallback* for when we genuinely can't reach GitHub, not the happy path.

The caller passes four things. Two are the **same for both skills** — `title` and `body` (described
under "Inputs you should already have" below) — and two **vary by skill**:

| | `feature-specify` | `bug-report` |
|---|---|---|
| **label** | `feature` | `bug` |
| **doc prefix** (fallback only) | `feature_` | `bug_` |

Everything else below is identical for both.

## Inputs you should already have

- **title** — a short, human-readable name (e.g. "Comment notifications", "Login fails on Safari").
- **body** — the filled-in template, as markdown. Note the template is intentionally **body-only**:
  it has no top-level `# H1` title and no date/version/status footer, because in a GitHub issue
  those are the title field and the issue's own metadata. You add the title back **only** in the
  file fallback (Step 3) — never inline it into the issue body.
- **slug** — kebab-case of the title (e.g. `comment-notifications`). Used only for the fallback filename.

## Step 1 — Can we reach GitHub?

Run these checks in order and stop at the first failure. Treat *any* failure as "GitHub
unavailable" and jump to the fallback in Step 3 — don't try to fix auth or add a remote yourself
unless the user asks.

```bash
gh --version        # gh installed?
gh auth status      # authenticated, token valid?
gh repo view --json nameWithOwner -q .nameWithOwner   # is this a GitHub repo with a reachable remote?
```

The third command is the one that catches the "no remote" case — it fails when the repo has no
GitHub remote or the remote isn't reachable. If it prints an `owner/repo`, GitHub is good to go.

## Step 2 — Create the issue (the happy path)

First make sure the label exists. `bug` ships with every GitHub repo, but `feature` does not, so
create it if it's missing (harmless if it already exists — ignore the "already exists" error):

```bash
gh label list --limit 200 --json name --jq '.[].name' | grep -qxF "<label>" || \
  gh label create "<label>" --color BFD4F2 --description "Filed via the base spec skills"
```

(Use `--json name --jq '.[].name'` for the existence check — the default `gh label list` output is
tab-separated columns, so a whole-line grep against it never matches and the create would run — and
error — on every repeat invocation. Keep `--limit 200` (the default is only 30) and `-F` so the
label is matched literally, not as a regex.)

Then create the issue and capture the URL it prints. The **title** goes in `--title` and the
**body** is the template as-is — don't prepend an H1 title to the body; that would duplicate the
issue title:

```bash
gh issue create --title "<title>" --label "<label>" --body "<body>"
```

For multi-line bodies, write the body to a temp file and pass `--body-file` instead of `--body` —
it avoids shell-quoting headaches with markdown:

```bash
gh issue create --title "<title>" --label "<label>" --body-file <tmpfile>
```

Report the created issue URL back to the user. Do **not** also write a file in this case — the
issue is the single source of truth.

## Step 3 — Fallback: write to docs/

Reach this only when Step 1 failed. Tell the user *why* you're falling back (e.g. "GitHub auth
isn't valid" or "this repo has no GitHub remote") so the degraded mode is never silent — they may
want to fix auth and re-run to get a real issue instead.

A file has to be self-contained, so here — and **only** here — give the body a title. Prepend the
title as an `# H1` (this is the one place the title belongs in the document), then the body:

```markdown
# <title>

<body>
```

Write that to:

```
docs/<prefix><slug>.md
```

e.g. `docs/feature_comment-notifications.md` or `docs/bug_login-fails-on-safari.md`. Create the
`docs/` directory if it doesn't exist. Don't add a date/version/status footer — git already tracks
the file's history, so a hand-maintained timestamp would just go stale. Report the path back to the
user, and mention they can move it to a GitHub issue later by re-running the skill once
`gh auth status` is clean.

## A note on judgment

These are guidelines, not a rigid script. If the user explicitly says "just write it to a file"
or "open it in repo X", honor that over the default. The goal is a spec that lands where the team
will actually see it — usually a GitHub issue, sometimes a file.
