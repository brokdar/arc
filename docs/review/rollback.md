# Rolling back a bad merge

Written against what exists on 2026-11-10; the facts it leans on are checked in
the places named, not restated from memory. Pick the case, then follow it top to
bottom.

| What shipped | Case |
|---|---|
| Code only (no migration, no `rebuild-streams`) | [A](#a-code-only) |
| Code plus an Alembic migration | [B](#b-code-plus-a-migration) |
| Anything, after `just rebuild-streams` ran | [C](#c-after-rebuild-streams) |

## First: stop more of it landing

Agents arm auto-merge on their own pull requests. If merges themselves are what is
going wrong, open an issue labelled `automerge-breaker`: while one is open,
`scripts/as-agent.sh` refuses `gh pr merge` (and the REST and GraphQL ways of
merging) and refuses to close, edit or delete that issue or its label, so no agent
can merge anything or switch the pause off.

Auto-merges that were **already armed** land by themselves when their checks go
green, with no further `gh pr merge` call. The daily `review-breaker` workflow
disarms them whenever it finds an open breaker issue. If you opened the issue by
hand, disarm them yourself (agents may run `--disable-auto` while paused):

```sh
gh pr list --state open --json number,autoMergeRequest --jq '.[] | select(.autoMergeRequest) | .number'
scripts/as-agent.sh gh pr merge <number> --disable-auto     # for each
```

The breaker also opens an issue on its own when most recent agent merges were
reverted or patched. **Closing the issue is how the operator re-enables merging**,
and it stays closed: it files again only if a merge that is not listed in the
closed issue goes bad (each issue lists its evidence). You can still merge by
hand while it is open.

## A. Code only

1. **Revert in git.** `main` is squash-only, so each PR is one commit and a revert
   is one `git revert <sha>`. Open it as a PR like any change:

   ```sh
   git switch -c revert/<what> origin/main
   git revert <sha-of-the-squash-commit>
   scripts/as-agent.sh git push -u origin revert/<what>
   scripts/as-agent.sh gh pr create --base main --title "revert: <what was reverted>" --body-file <path>
   ```

   The title must be `revert: <what was reverted>`, lowercase after the colon:
   GitHub's default `Revert "…"` fails the `pr-title` check, which is required.
   A revert that touches an owned path (see `.github/CODEOWNERS`) needs the
   operator's approval like any other change to that path.

2. **Put the previous build on the server.** There are **no release tags yet**
   (`git tag` is empty, checked 2026-11-10), so "redeploy the previous tag" does
   not exist until the first `v*` tag is pushed. Until then, rebuild the last good
   commit and ship it, as for any unreleased build. (The deploy notes and the
   `docker-compose.override.yml` that reads `ARC_TAG` live under
   `docs/deploy-odroid/`, which is local to the operator's checkout and not in git;
   the tracked `docker-compose.yml` has no `image:` keys. On a fresh clone, copy
   that override from the operator's checkout first.)

   ```sh
   git switch --detach <last-good-sha>
   just images                  # buildx cross-build, arm64, tag "dev"
   just ship-images <ssh-host>  # docker save | ssh <host> docker load
   ```

   then on the server: `ARC_TAG=dev docker compose up -d --no-build`.

   Once release tags exist, the previous release is already in GHCR and the
   server only needs `ARC_TAG=<previous tag> docker compose pull && docker compose up -d --no-build`.

## B. Code plus a migration

The API runs `alembic upgrade head` on every boot (`docker-compose.yml`), so
deploying the bad build already moved the schema. An older image against a newer
schema can fail to boot or misread rows. Every migration here has a `downgrade()`
and CI runs head → base → head (`backend/tests/integration/test_migrations.py`),
so the schema can be stepped back — but only by code that **contains** the
migration, and `api` restarts itself (`restart: unless-stopped`) with
`alembic upgrade head` in front of its command. So the schema must not be stepped
back until the good images are ready to start in the same minute:

1. **Build and ship the good images first**, while the bad ones keep running
   (`docker load` retags without touching a running container): revert in git,
   `just images`, `just ship-images <ssh-host>` as in case A. Do not touch the
   server's containers yet.
2. **Then, back to back**, step the schema back with the bad (newer) image still
   running and start the good one:

   ```sh
   docker compose exec api alembic downgrade <good-revision>   # or -1
   ARC_TAG=dev docker compose up -d --no-build
   ```

   `<good-revision>` is the newest file in
   `backend/app/persistence/alembic/versions/` at the last good commit. Any
   restart between the two commands re-applies the migration, so do not stop to
   check anything in between.
3. **A downgrade can drop data** (a column added by the migration is gone, with
   what it held). If that matters, restore from a dump instead — below.

**Take a dump before deploying any migration.** There is no scheduled backup:

```sh
docker compose exec db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > arc-$(date +%F).sql
```

To restore onto a fresh stack (before first login):
`docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < arc-<date>.sql`.
Original ride files live in `data/originals/` and rebuild every stream and metric,
but not history that exists only in the database (anchors, evaluations, the audit
trail), which is why the dump matters.

## C. After `rebuild-streams`

**Do not roll the image back and leave it there.** A rebuilt parquet file carries
channels an older image has no enum member for; it reads the file as missing and
every rebuilt session loses its chart and stream metrics until a newer image is
back (`justfile`, `rebuild-streams`). Nothing is destroyed — the originals are
untouched — but running the older image over rebuilt files is an outage.

Be honest about the shape of the fix: if the bad merge was the parser change that
added the channel, **reverting it produces exactly that older image**, so there is
an outage window either way. Fix forward to keep it short: revert the code as in
case A, build it, deploy it, and run `just rebuild-streams` immediately on top, so
the files are re-derived by the reverted parser. Sessions show no chart until that
finishes.

## Afterwards

Say what happened in the revert PR's description (what broke, how it was noticed,
what test would have caught it) and, if a test would have, add it in the same PR.
A revert with no test is the same bug waiting.
