import createFetchClient from "openapi-fetch";
import createClient from "openapi-react-query";

import { env } from "@/env";
import type { paths } from "@/generated/api/schema";

/** Plain fetch client — use in server components and route handlers. */
export const apiClient = createFetchClient<paths>({
  baseUrl: env.NEXT_PUBLIC_API_BASE_URL,
  // Resolve fetch at call time, not module-import time, so interceptors
  // installed later (MSW in tests) are honored.
  fetch: (request) => globalThis.fetch(request),
});

/** TanStack Query bindings — use in client components ($api.useQuery, ...). */
export const $api = createClient(apiClient);
