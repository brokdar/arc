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
 * - every 200 lists the sessions still active and the device holding each
 *   (`session_start`'s `device_id`, then the latest `session_claim`); a
 *   finish ends it and a reopen brings it back. The sets each session holds
 *   are kept as the patches fold, so `GET /sessions/{id}` can serve them
 *   (`syncedSessionRead` in `handlers.ts`).
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
  /** What the server holds per session client id. */
  synced: Map<string, SyncedSession>;
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
    synced: new Map(),
    requests: 0,
  };
}

export interface SyncedSession {
  /** The server's id, minted on `session_start`. */
  id: string;
  clientId: string;
  plannedSessionId: string | null;
  deviceId: string | null;
  startTime: string;
  endTime: string | null;
  /** Merged set fields by set client id; a deleted set is absent. */
  sets: Map<string, Partial<Schemas["SyncSet"]>>;
}

let state = fresh();
let minted = 0;

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
  const pending: Mutation[] = [];
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
    pending.push(mutation);
    seen.add(mutation.mutation_id);
    applied.push(mutation.mutation_id);
    results.push({ mutation_id: mutation.mutation_id, status: "applied" });
  }
  state.applied.push(...applied);
  state.sessions = sessions;
  for (const mutation of pending) fold(mutation);
  return {
    status: 200,
    body: {
      schema_version: state.version,
      results,
      active_sessions: activeSessions(),
    },
  };
}

function fold(mutation: Mutation): void {
  if (mutation.kind === "note_upsert") return;
  const key = mutation.session_client_id;
  if (mutation.kind === "session_start" && !state.synced.has(key)) {
    minted += 1;
    state.synced.set(key, {
      id: `0199d000-0000-7000-8000-${minted.toString().padStart(12, "0")}`,
      clientId: key,
      plannedSessionId: mutation.planned_session_id ?? null,
      deviceId: mutation.device_id ?? null,
      startTime: mutation.start_time,
      endTime: null,
      sets: new Map(),
    });
    return;
  }
  const session = state.synced.get(key);
  if (!session) return;
  switch (mutation.kind) {
    case "session_claim":
      session.deviceId = mutation.device_id;
      return;
    case "session_finish":
      session.endTime = mutation.end_time;
      return;
    case "session_reopen":
      session.endTime = null;
      return;
    case "set_upsert":
      session.sets.set(mutation.set_client_id, {
        ...(session.sets.get(mutation.set_client_id) ?? {}),
        ...mutation.set,
      });
      return;
    case "set_delete":
      session.sets.delete(mutation.set_client_id);
      return;
    default:
      return;
  }
}

/** The sessions between Start and Finish, oldest first, as `/sync` lists them. */
export function activeSessions(): Schemas["ActiveSessionRead"][] {
  return [...state.synced.values()]
    .filter((session) => session.endTime === null)
    .map((session) => ({
      session_client_id: session.clientId,
      session_id: session.id,
      planned_session_id: session.plannedSessionId,
      device_id: session.deviceId,
      start_time: session.startTime,
    }));
}

/** The synced session with this server id, if any. */
export function syncedSession(id: string): SyncedSession | undefined {
  return [...state.synced.values()].find((session) => session.id === id);
}

function refusal(mutation: Mutation, sessions: Set<string>): string | null {
  if (mutation.kind === "note_upsert" || mutation.kind === "session_start") {
    return null;
  }
  return sessions.has(mutation.session_client_id)
    ? null
    : `no session with client id ${mutation.session_client_id}`;
}
