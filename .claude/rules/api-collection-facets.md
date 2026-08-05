---
paths: backend/app/api/routes/**
---

# A facet of a collection does not live under `/{id}`

`GET /resources/labels` also matches `GET /resources/{resource_id}`. FastAPI
picks whichever route is declared first, so the facet works — but only for the
method it declares. Every *other* method falls through to the id route and
answers **422 about the path parameter's syntax**, where 405 is the true
answer. Schemathesis' `unsupported_method` check fails on exactly that.

So a facet of the collection as a whole gets its own path outside the id
namespace — `GET /api/v1/workout-labels`, not `GET /api/v1/workouts/labels` —
and Starlette's own 405 becomes correct for free. A **sub-resource of one
member** (`/planned-sessions/{id}/intents`) is fine as-is: it has one more
segment than the id route, so nothing collides.

This has come up twice. In WP-1 the id route itself was the problem
(`PUT /anchors/current` answering 422 about uuid syntax), and the fix was to
type the path parameter `str` and answer 405 from real handlers — correct
there, because the refusals are the point (D36, D39). In WP-2 the collision
was accidental and the path moved instead (D50). Prefer moving the path: four
refusal handlers per shadowed facet is a lot of code to say "this route should
never have been in the id namespace".
