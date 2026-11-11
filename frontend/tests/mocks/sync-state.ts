import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];
type Mutation = Schemas["Mutation"];

/**
 * The server side of `POST /sync`, small enough to reason about and faithful
 * where the outbox depends on it (`backend/app/services/sync.py`):
 *
 * - a mutation id seen before answers `already_applied` and changes nothing;
 * - a batch is all or nothing: the first mutation the server cannot apply — a
 *   session-scoped one for a session no `session_start` created — is a 422
 *   naming it, and nothing in the batch is kept;
 * - a `schema_version` above the server's is the 409 with
 *   `code: "schema_version_unsupported"`;
 * - without a session the answer is a 401 and nothing is applied.
 *
 * `version`, `signedIn` and `healthy` are the knobs a test turns to stand in
 * for an older server, an expired login and a dead network.
 */
export interface SyncServerState {
  version: number;
  signedIn: boolean;
  healthy: boolean;
  /** Every mutation id applied, in the order the server applied them. */
  applied: string[];
  /** Session client ids a `session_start` created. */
  sessions: Set<string>;
  /** How many `POST /sync` requests reached the server. */
  requests: number;
}

function fresh(): SyncServerState {
  return {
    version: 1,
    signedIn: true,
    healthy: true,
    applied: [],
    sessions: new Set(),
    requests: 0,
  };
}

let state = fresh();

export function syncServer(): SyncServerState {
  return state;
}

export function resetSyncState(): void {
  state = fresh();
}

export type SyncAnswer =
  | { status: 200; body: Schemas["SyncResult"] }
  | { status: 401; body: Schemas["ErrorDetail"] }
  | { status: 409; body: Schemas["SchemaVersionUnsupported"] }
  | { status: 422; body: Schemas["SyncRejected"] };

/** Apply one batch as the server would; see the module docstring. */
export function applyBatch(batch: Schemas["SyncBatch"]): SyncAnswer {
  state.requests += 1;
  if (!state.signedIn) {
    return { status: 401, body: { detail: "Not authenticated" } };
  }
  if (batch.schema_version > state.version) {
    return {
      status: 409,
      body: {
        detail: `schema_version ${batch.schema_version} is not one this server understands`,
        code: "schema_version_unsupported",
        server_schema_version: state.version,
      },
    };
  }
  if (batch.mutations.length > 500) {
    return {
      status: 422,
      body: { detail: [{ loc: ["body", "mutations"], msg: "too long" }] },
    };
  }
  const seen = new Set(state.applied);
  const sessions = new Set(state.sessions);
  const applied: string[] = [];
  const results: Schemas["MutationResultRead"][] = [];
  for (const mutation of batch.mutations) {
    if (seen.has(mutation.mutation_id)) {
      results.push({
        mutation_id: mutation.mutation_id,
        status: "already_applied",
      });
      continue;
    }
    const refused = refusal(mutation, sessions);
    if (refused) {
      return {
        status: 422,
        body: {
          detail: refused,
          code: "mutation_rejected",
          mutation_id: mutation.mutation_id,
        },
      };
    }
    if (mutation.kind === "session_start") {
      sessions.add(mutation.session_client_id);
    }
    seen.add(mutation.mutation_id);
    applied.push(mutation.mutation_id);
    results.push({ mutation_id: mutation.mutation_id, status: "applied" });
  }
  state.applied.push(...applied);
  state.sessions = sessions;
  return { status: 200, body: { schema_version: state.version, results } };
}

function refusal(mutation: Mutation, sessions: Set<string>): string | null {
  if (mutation.kind === "note_upsert" || mutation.kind === "session_start") {
    return null;
  }
  return sessions.has(mutation.session_client_id)
    ? null
    : `no session with client id ${mutation.session_client_id}`;
}
