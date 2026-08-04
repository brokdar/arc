# Frontend

Next.js 16 (App Router) UI for arc. TypeScript, Tailwind 4, shadcn/ui over
Base UI, TanStack Query against a generated, typed API client.

Package manager is **bun** — never npm, pnpm or yarn. Repo-wide workflows go
through the root `justfile` (`just dev-web`, `just check`); the scripts below
are the direct equivalents.

> This Next.js major is not the one in your training data: APIs, conventions
> and file layout differ. Read the relevant guide in
> `node_modules/next/dist/docs/` before writing app code — see `AGENTS.md`.

## Commands

```bash
bun install            # install dependencies (bun.lock is authoritative)
bun dev                # dev server → http://localhost:3000
bun run build          # production build
bun run start          # serve the production build

bun run lint           # Biome lint + format check
bun run lint:fix       # Biome, writing fixes
bun run format         # Biome formatter only

bun run type-check     # tsgo (native TS 7 compiler) --noEmit
bun run type-check:tsc # same, via classic tsc — for when tsgo disagrees

bun run test           # Vitest, once
bun run test:watch     # Vitest, watching
bun run test:coverage  # Vitest with V8 coverage
bun run test:e2e       # Playwright
```

## Structure

```
app/                 App Router: layout, providers, pages (/, /login, /items)
components/
  ui/                shadcn/ui primitives (Base UI — `render={...}`, NOT `asChild`)
  auth/              login form + AuthGuard
  items/             WP-0 worked example; deleted in WP-1
lib/api/client.ts    openapi-fetch client + `$api` TanStack Query bindings
env.ts               validated, build-time-baked env (@t3-oss/env-nextjs + zod)
generated/api/       openapi.json + schema.d.ts — GENERATED, never hand-edit
tests/mocks/         typed MSW handlers + server
e2e/                 Playwright specs (UI-only and @fullstack)
```

Component tests live next to what they test (`*.test.tsx`).

## The typed API contract

`generated/api/` is derived from the backend's OpenAPI schema and committed.
Never edit it by hand. After any backend endpoint or schema change:

```bash
just api-sync    # backend app → generated/api/openapi.json → schema.d.ts
```

That runs `scripts/generate-api-types.sh`, which exports the schema offline
from the FastAPI app (no running server), regenerates `schema.d.ts` with
`openapi-typescript`, and formats the output so Biome stays happy. Commit the
result: `just api-check` and CI fail on drift.

Use the client from `lib/api/client.ts` — `$api.useQuery` / `$api.useMutation`
in client components, `apiClient` in server components and route handlers. It
sends credentials, because the API authenticates with a session cookie.

`NEXT_PUBLIC_API_BASE_URL` is baked in at build time. Empty means *same
origin* — the browser calls `/api/...` on whatever host served the page and
Caddy forwards it — which is how the Docker stack builds this app. An absolute
URL is for running without the proxy (bare-metal `bun dev` against a local
API), which is the default outside Docker.

## Testing

- **Component tests** (Vitest + Testing Library, jsdom): mock the *network*
  with the typed MSW handlers in `tests/mocks/handlers.ts` (openapi-msw), so a
  handler that contradicts the API contract fails type-checking. Never mock
  `lib/api/client.ts`. Untyped responses only via `http.untyped`, for
  simulating infrastructure failures.
- **Playwright, two modes** (`playwright.config.ts`):
  - default — UI-only, against `bun run start`, no backend; `@fullstack` specs
    are excluded. This is `just e2e`.
  - `E2E_FULLSTACK=1` — runs *only* `@fullstack` specs against the running
    Compose stack through Caddy on `http://localhost`, logging in once in a
    `setup` project and replaying the session via `storageState`. This is
    `just smoke`, and it needs `E2E_PASSWORD` to match the password behind
    `AUTH__PASSWORD_HASH` in `.env`. Keep this suite tiny — it verifies wiring,
    not logic.

## See also

`../README.md` (quick start, services, layout) and `../AGENTS.md` (conventions
and commands across both projects).
