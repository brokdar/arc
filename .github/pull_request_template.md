<!--
The PR title must be a Conventional Commit — `feat(wp-1): add session matching`.
`main` is squash-only, so this title becomes the commit subject on main and is
what the changelog is generated from. CI fails the PR if it does not parse.
-->

## What

<!-- What does this PR change, and why? Write it as prose: this is the raw
     material for the CHANGELOG.md entry.

     One line per paragraph, no hard wrapping — GitHub renders every newline
     inside a paragraph as a line break, so wrapped prose comes out ragged. -->

## Checklist

- [ ] Backend endpoint/schema changes: ran `just api-sync` and committed `frontend/generated/api/`
- [ ] Model changes ship with an Alembic migration
- [ ] New settings added to both `app/core/config.py` and `.env.example`
- [ ] `just check` passes locally
