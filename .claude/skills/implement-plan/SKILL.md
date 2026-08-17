---
name: implement-plan
description: >
  Build a feature plan's pull requests — each in its own worktree, developed TDD
  against its acceptance criteria and edge cases, gated by `just gate` and then by
  an independent reviewer, then pushed as its own PR with CI driven green. PRs the
  DAG waits on auto-merge so the next group can start; leaf PRs stay open for you.
  Use when asked to implement or build out a plan, or ship a feature's PRs. (To
  write the plan first, that is `feature-plan`.)
argument-hint: "[<slug>-plan.md] [branch to build | verify]"
allowed-tools: Read, Bash(git status:*), Bash(git worktree:*), Bash(git log:*), Bash(gh pr list:*), Bash(node scripts/parse-plan.mjs:*), Bash(bash scripts/docker-lock.sh status:*), Workflow, TaskOutput
---

# Build a plan's pull requests

This launches the `implement-plan` workflow, which runs the pipeline you cannot run in your head.
Per PR: a fresh developer implements it TDD in its own worktree; a cheap seat runs **`just gate`** and
commits only if it passes; a **separate agent with no editing tools** judges the committed diff against
that PR's acceptance criteria and their edge cases; a **third** agent fixes only what was rejected,
and the review repeats — bounded at two cycles, then a hard stop with no PR opened. Only then is it
pushed and opened, and only then is CI driven green.

No agent ever reviews or fixes its own work. Every seat is a fresh context, and `arc-reviewer` is
given `tools: Read, Bash, Glob, Grep` — no Write, no Edit — so repairing its own findings would take a
deliberate detour through the shell, which its instructions forbid. The gate seat is neither judge nor
developer: it runs one command, commits on success, and is forbidden to fix anything it sees fail.

**Mechanical steps are scripts, not judgement.** The workflow itself has no shell — it can only spawn
agents — so each of these is a tested script that a cheap seat runs and reports the exit code of:

| | |
| --- | --- |
| `node scripts/parse-plan.mjs <plan>` | plan → JSON, cross-checked against `gh`/`git`, plan snapshotted |
| `just gate` | the whole pre-review gate, one exit code (see the recipe for its tiers) |
| `node scripts/ci-status.mjs <pr>` | green / red / never-registered / pending, always returns |
| `bash scripts/docker-lock.sh` | exclusive use of the fixed compose ports, across worktrees |

## Before you launch

1. **There must be a plan.** This skill executes one; it does not write one. If `$ARGUMENTS` names no
   plan, ask for the path — or point at `/feature-plan` if they want one written first.
2. **Nothing else may be running.** Two `implement-plan` runs in one checkout will fight over Docker,
   the worktree directory and the git index. Check `/workflows`, and `bash scripts/docker-lock.sh
   status`. Every seat that touches Docker is *instructed* to take that lock, so two obedient runs
   refuse each other rather than colliding silently — but the recipes themselves do not take it, so a
   human running `just test-int` in another terminal is not covered, and nothing protects the index.
3. **Build the args as a JSON object yourself.** Never pass `$ARGUMENTS` through as a string. The
   script parses a bare string as a safety net, but the blast radius of a mis-parse here is real PRs
   on a real remote.
   - `plan` (**required**) — `<slug>-plan.md` in the repository root.
   - `onlyBranch: "feat/…"` — build one PR only. This is the re-entry after a hard stop.
   - `verifyFeature: true` — implement nothing; verify the feature-scoped criteria against `main`.
     Requires every PR merged.
   - `autoMerge: false` — never merge anything, even a prerequisite. The DAG then stops after the
     first group and waits for you.
   - `skipGateBaseline: true` — do not measure the gate before building. Only when you already know
     it is green and want the ~4 minutes back.
4. **Check the tree.** `git status` in the main checkout and `git worktree list`. Each PR lives in
   `.claude/worktrees/<branch with its slash flattened to a dash>` — branch `feat/x` is in
   `.claude/worktrees/feat-x`. A hard-stopped earlier run leaves work there, so read the previous
   run's `recovery` line before re-launching the same branch.
5. **Dry-run the parse if the plan is new**: `node scripts/parse-plan.mjs <plan>` exits 2 and names
   every missing marked line, plus any bullet under **Acceptance** that belongs to no criterion. That
   catches the *structural* defects for free. The rest — the title and branch rules, positional
   labels, duplicate titles or branches, an unresolved `(confirm)`, a `## Why` under 80 characters, a
   dangling `Depends`, a cycle, two migrations in one group — are checked at launch, and each refuses
   the whole run.
6. **Export `E2E_PASSWORD` yourself, in the shell that launches this**, if you have it — a skill
   cannot set it for you, and shell state does not survive between commands. Without it the
   local-verification fallback records `just smoke` as NOT_RUN rather than passing it.
7. **Say which PRs will be built, and that some will auto-merge**, then launch. This one touches the
   remote and `main`.

## Launch

```
Workflow({
  scriptPath: ".claude/workflows/implement-plan.js",
  args: { plan: "wellness-plan.md" }
})
```

It runs in the background; `/workflows` shows live progress. The first line logged is
`Launch: raw=… parsed=… plan=…`, followed by the derived groups — for anything narrower than the
whole plan, a quick non-blocking `TaskOutput` a few seconds in confirms the scope.

## The gate's baseline

Before it builds anything, the run measures `just gate` once on this checkout. That exists because
the gate is a **hard** gate: red means the pull request stops with nothing pushed, which is right
when the PR caused it and catastrophic when it did not — every PR in the plan would spend two opus
fix agents on someone else's breakage and then hard-stop.

- **Green** — nothing changes, and any red gate from then on belongs to the PR that produced it.
- **Red** — the failing names are logged and carried into every gate seat. A PR whose gate fails on
  exactly those still commits, is reviewed, and is opened; the reviewer is told those failures are
  not its to answer for, and `gatePreexisting` names them in the report. Anything a PR breaks
  *beyond* that list is still its own, and still stops it.

A seat that claims "pre-existing" for a failure the baseline did not have is refused
(`gate-red-claimed-preexisting`) — the claim is checked against the measurement, not taken.

Fix the baseline when you can. It is measured on the checkout you launch from, while the worktrees
are cut from `origin/main`, so the two can differ; the log says which state it measured.

## What merges itself, and what waits for you

Concurrency is **derived from each PR's `Depends:`**, never hand-numbered. A PR the DAG is actually
waiting on — something unmerged depends on it — is squash-merged by its own finish seat once it is
review-approved and CI-green, and the merge verdict comes back as data on that PR's result. The next
group's setup seat then confirms the prerequisite really is on `origin/main` before cutting a worktree
from it, so two independent checks have to agree. A **leaf** PR — nothing unmerged depends on it — is
never merged; it stays open for you to read.

If a prerequisite will not merge, the workflow stops rather than build the next group off a `main`
that lacks what it needs.

When the last PR is merged, launch once more with `verifyFeature: true`. That pass is not
bookkeeping: per-PR review structurally cannot see cross-PR gaps — duplicated work, one surface
contradicting another, a capability that reached the API but not MCP. It runs against integrated
`main` and is the only gate that catches them.

## When CI never runs

`scripts/ci-status.mjs` exiting **2** (`NO_RUNS`) means no workflow run ever registered for the PR's
head SHA — Actions did not start (budget, spending limit, workflows disabled). It is never concluded
from a single observation: the script must watch zero runs for at least a minute, and either for its
whole grace window or against a head commit older than it. That is deliberately distinct from red:
nothing is wrong with the code, and sending a fix agent to hunt a defect that does not exist wastes a
cycle.

The workflow then verifies the PR locally against everything CI would have run (`just gate`,
`just test-int`, `just e2e`, `just smoke`, schemathesis where the PR touched a route or schema) under
the Docker lock, and posts one comment on the PR recording the commit SHA and each check as
PASS / FAIL / **NOT_RUN with the reason** / **PREEXISTING with the `main` SHA it reproduces on**.

That honesty matters more than the coverage. A comment standing in for CI on a public PR that claims
coverage it did not achieve is worse than no comment. Two things it will tell you rather than paper
over: `just smoke` needs `E2E_PASSWORD` to match the bcrypt hash in `.env`, and a tier that fails
identically on `main` is *not* this PR's failure — it is recorded PREEXISTING with the control SHA,
because a DAG once halted over four e2e tests that were already broken.

A local verification that had to skip anything **never auto-merges**: `notRun` non-empty means a human
decides whether that evidence is enough. The same applies when its own `checks` list contradicts
`notRun` — a tier written as NOT_RUN or FAIL there but absent from `notRun` is counted as not run, and
logged.

## After it returns

- **`merged`** — prerequisites that squash-merged. Relay number, URL, title.
- **`open`** — PRs awaiting your review, with `reviewLoops` / `ciLoops` / `gateLoops` and `ciMode`. A
  PR that needed two review cycles deserves a closer human read than one that passed first time, and
  `ciMode: "local"` or `"local-partial"` means CI never ran and the evidence is the comment on the PR
  — check `notRun` and `preexisting`. **`mergeFailed`** means the workflow tried to squash-merge this
  one and could not: `mergeDetail` says why, and `nextAction` will name it. `worktreeKept` means the
  finish seat refused to remove the worktree and said why.
- **`stopped`** — each carries a `reason` and a `recovery` line written for the human who decides.
  Relay both verbatim.
  - `review rejected …` — criteria unmet after two fix cycles. **No PR was opened**, and the work is
    committed on the branch but unpushed. Surface `gaps` (criteria) and `processNotes` (everything
    else). Do not silently re-launch; this needs a decision about whether the code or the criterion
    is wrong.
  - `gate red …` / `gate-red-after-review-fix` / `gate-red-after-ci-fix` — `just gate` will not pass.
    `gateExit` is in the report; run `just gate` in the worktree to see it yourself.
  - `commit-refused` — the gate passed but the commit seat found a path in the tree it did not
    recognise. Look before you re-launch: something else wrote in that worktree.
  - `CI RED/UNKNOWN …` — the PR is open but red. CI runs the tiers `just gate` omits, so this is
    usually a real defect. The worktree was removed (the work is pushed); the recovery line names the
    two commands that restore it.
  - `prerequisite-not-on-main` — the setup seat could not find the prerequisite's *title* on
    `origin/main`. Since `main` is squash-only, that subject is what a merge leaves behind; if it is
    genuinely there, the seat misread and this is worth a look before re-launching.
  - `review-self-contradictory` — the review returned APPROVED while marking a criterion
    NOT_FULFILLED or PARTIAL. Nothing was pushed. Read `criteria` in the report.
  - `commit-sha-missing` / `commit-refused` — the gate passed but the commit did not land, or landed
    without a SHA the later steps could name. Look at the worktree: either a pre-commit hook is
    refusing, or something unexpected was in the tree.
  - `worktree-dirty` — a worktree from an earlier run holds uncommitted work that exists nowhere
    else. Nothing was built. Keep it or discard it by hand, then re-launch with `onlyBranch`.
  - `worktree-unsafe` — the worktree is not on the branch it should be on.
  - `local-ci-red` / `local-ci-failed` / `local-ci-unrecorded` — the no-CI fallback found a failure,
    could not run, or could not post its evidence.
  - `implement-failed` / `*-agent-died` / `pr-failed` / `worktree-setup-failed` — usually transient.
    Check the worktree and `gh pr list` first: partial work left behind must be finished or reset, or
    the re-run builds on top of it.
- **Plan defects caught before any agent ran** — `plan-defects` (the parser's own stderr, naming
  every missing marked line), `open-questions`, `no-why`, `bad-pr-title`, `positional-label`,
  `unknown-dependency`, `dependency-cycle`, `two-migrations-in-group`.
- **`parse-refused`** — `gh` or `origin/main` was unreachable (exit 3). The run refuses rather than
  guess at what is merged, because guessing rebuilds a shipped PR or starts a dependent too early.
- **`plan-not-found`** — the plan path does not exist (exit 4). Usually a typo, or a bare-string
  launch that was never a path.
- **`bad-plan-path`** — what was passed is not a `<slug>.md` path inside the repository. Nothing ran.
- **`duplicate-title`** / **`duplicate-branch`** — two PRs in the plan share a title or a branch.
- Anything else in `stopped` carries its own `reason` and `recovery`; the reasons are the strings the
  workflow logs, and every one of them names what to look at.
- **`parse-echo-corrupt`** — the parse seat's copy of the plan JSON did not match the count the script
  printed. Re-launch; if it repeats, run the parser by hand and read it.

Do not re-run a PR that is already open and green. To redo one, close its PR and delete the branch.

## Cleaning up

Worktrees are removed automatically once their work is on the remote: on success, and on the stops
where everything is pushed (a red or unknown CI, and the local-verification failures). The stops that
hold **unpushed** work keep the worktree deliberately — a rejected review, a red gate, a CI fix that
was committed but never pushed — because there the tree is the only copy.

`/clean-gone` deletes local branches whose upstream is gone — after a squash merge, exactly the merged
ones — along with any worktree still attached, confirming first.
