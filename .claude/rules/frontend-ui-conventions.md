---
paths: frontend/**
---

# arc UI conventions

1. **Deep-link what a person would bookmark; keep transient state out of the
   URL.** Sub-views that survive a reload are real routes (`/sessions/{id}`,
   `/sessions/{id}/power`), not client-side tab state; calendar position lives
   in the query string (`/calendar?week=2026-08-03`). Modal, dropdown, hover
   and selection state never enter the URL — they are not addressable places.
2. **Three overlay tiers, chosen by purpose.** Inline panel anchored under its
   trigger for filters and toggles; centred modal for editing one record; a
   route for anything worth linking to. A fourth tier is reserved for WP-5: a
   *floating, draggable window* — not a modal — for the map, because the
   athlete needs map and chart visible at once.
3. **Empty states name the missing input and the action that supplies it.**
   Not "No data yet" but "Add an FTP anchor to see power zones", with the
   control beside it — an empty state that names no remedy is a dead end.
4. **Metric grids hold their positions.** A missing value renders a
   placeholder (`components/design/not-assessed.tsx`) in its fixed slot; it
   never collapses the grid or reflows its neighbours, because position is how
   a returning eye finds a number.
5. **Numerals are monospace.** Every duration, date, watt, percentage and
   count renders in `font-mono` (JetBrains Mono, wired in `globals.css`) —
   restated here so it survives the next component.
6. **One dark scheme, declared.** arc is dark-only (D59): no light theme, no
   toggle, `color-scheme: dark` — never style for a light canvas.
