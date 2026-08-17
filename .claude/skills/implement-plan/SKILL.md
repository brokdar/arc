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
commits only if it passes; a **separate agent with no write tools** judges the committed diff against
that PR's acceptance criteria and their edge cases; a **third** agent fixes only what was rejected,
and the review repeats — bounded at two cycles, then a hard stop with no PR opened. Only then is it
pushed and opened, and only then is CI driven green.

No agent ever reviews or fixes its own work. That is structural: every seat is a fresh context, and
`arc-reviewer` has `tools: Read, Bash, Glob, Grep` — it *cannot* edit what it finds. The gate seat is
neither judge nor developer: it runs one command, commits on success, and is forbidden to fix
anything it sees fail.

**Mechanical steps are scripts, not judgement.** The workflow itself has no shell — it can only spawn
agents — so each of these is a tested script that a cheap seat runs and reports the exit code of:

| | |
| --- | --- |
| `node scripts/parse-plan.mjs <plan>` | plan → JSON, cross-checked against `gh`/`git`, plan snapshotted |
| `just gate` | the whole pre-review gate, one exit code, non-mutating |
| `node scripts/ci-status.mjs <pr>` | green / red / never-registered / pending, always returns |
| `bash scripts/docker-lock.sh` | exclusive use of the fixed compose ports, across worktrees |

## Before you launch

1. **There must be a plan.** This skill executes one; it does not write one. If `$ARGUMENTS` names no
   plan, ask for the path — or point at `/feature-plan` if they want one written first.
2. **Nothing else may be running.** Two `implement-plan` runs in one checkout will fight over Docker
   and the worktree directory. Check `/workflows`, and `bash scripts/docker-lock.sh status`. The
   Docker tiers now take that lock, so the collision is refused rather than silent — but two runs
   still make a mess of everything else.
3. **Build the args as a JSON object yourself.** Never pass `$ARGUMENTS` through as a string. The
   script parses a bare string as a safety net, but the blast radius of a mis-parse here is real PRs
   on a real remote.
   - `plan` (**required**) — `<slug>-plan.md` in the repository root.
   - `onlyBranch: "feat/…"` — build one PR only. This is the re-entry after a hard stop.
   - `verifyFeature: true` — implement nothing; verify the feature-scoped criteria against `main`.
     Requires every PR merged.
   - `autoMerge: false` — never merge anything, even a prerequisite. The DAG then stops after the
     first group and waits for you.
4. **Check the tree.** `git status` in the main checkout and `git worktree list`. Each PR lives in
   `.claude/worktrees/<branch>`; a hard-stopped earlier run leaves work there, so read the previous
   run's `recovery` line before re-launching the same branch.
5. **Dry-run the parse if the plan is new**: `node scripts/parse-plan.mjs <plan>` exits 2 and names
   every missing marked line. That is the whole plan-defect check, for free, before any agent runs.
6. **Export `E2E_PASSWORD`** if you have it, so the local-verification fallback can actually run
   `just smoke` instead of recording it NOT_RUN.
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

`scripts/ci-status.mjs` exiting **2** means no workflow run ever registered for the PR's head SHA,
long after it was pushed — Actions did not start (budget, spending limit, workflows disabled). That is
deliberately distinct from red: nothing is wrong with the code, and sending a fix agent to hunt a
defect that does not exist wastes a cycle.

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
decides whether that evidence is enough.

## After it returns

- **`merged`** — prerequisites that squash-merged. Relay number, URL, title.
- **`open`** — PRs awaiting your review, with `reviewLoops` / `ciLoops` / `gateLoops` and `ciMode`. A
  PR that needed two review cycles deserves a closer human read than one that passed first time, and
  `ciMode: "local"` or `"local-partial"` means CI never ran and the evidence is the comment on the PR
  — check `notRun` and `preexisting`.
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
    usually a real defect. The worktree was removed (the work is pushed); the recovery line has the
    one command that restores it.
  - `local-ci-red` / `local-ci-failed` / `local-ci-unrecorded` — the no-CI fallback found a failure,
    could not run, or could not post its evidence.
  - `implement-failed` / `*-agent-died` / `pr-failed` / `worktree-setup-failed` — usually transient.
    Check the worktree and `gh pr list` first: partial work left behind must be finished or reset, or
    the re-run builds on top of it.
- **Plan defects caught before any agent ran** — `plan-defects` (the parser's own stderr, naming
  every missing marked line), `open-questions`, `no-why`, `bad-pr-title`, `positional-label`,
  `unknown-dependency`, `dependency-cycle`, `two-migrations-in-group`.
- **`parse-refused`** — `gh` or `origin/main` was unreachable. The run refuses rather than guess at
  what is merged, because guessing rebuilds a shipped PR or starts a dependent too early.
- **`parse-echo-corrupt`** — the parse seat's copy of the plan JSON did not match the count the script
  printed. Re-launch; if it repeats, run the parser by hand and read it.

Do not re-run a PR that is already open and green. To redo one, close its PR and delete the branch.

## Cleaning up

Worktrees are removed automatically once their work is on the remote — on success, and on the stops
that happen after the push. `/clean-gone` deletes local branches whose upstream is gone — after a
squash merge, exactly the merged ones — along with any worktree still attached, confirming first.
