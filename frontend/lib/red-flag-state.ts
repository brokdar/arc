import type { RedFlagKind } from "@/components/strength/safety/safety-model";
import type { ActiveSession } from "@/components/strength/session-model";
import type { components } from "@/generated/api/schema";

type Athlete = components["schemas"]["AthleteRead"];

/** A red flag that is standing, and where Today learnt of it. */
export interface ActiveRedFlag {
  readonly kind: RedFlagKind;
  /**
   * Emergency symptoms and a "severe" flag: the ones that put a confirmation
   * between the athlete and the next session. A milder flag only shows.
   */
  readonly severe: boolean;
  readonly note: string | null;
  /**
   * `server` — the athlete record says so, and "All better" can lower it.
   * `phone` — a session on this phone ended on a red flag whose upload has
   * not landed, so the server does not know yet and there is nothing to lower.
   */
  readonly source: "server" | "phone";
}

/** How long a phone's own record of a red-flag stop is believed unaided. */
export const LOCAL_FLAG_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * Whether a red flag is standing, from the server's word and the phone's.
 *
 * The server wins when it says the flag is up. When it says the flag is down
 * the phone's record still counts if the upload that raises it is *still in
 * the outbox*: that is precisely the minutes after a gym stop in which the
 * athlete could start another session with nothing on screen (strength UAT
 * B10). With no server answer at all (offline) a stop within the last day
 * counts, because nothing says it was cleared. A record older than that, or
 * one whose upload has landed and been lowered since, does not — `redFlag`
 * stays on a stored session for good, so it is never believed on its own.
 */
export function activeRedFlag(input: {
  readonly athlete: Athlete | undefined;
  readonly local: readonly ActiveSession[];
  readonly flagUploadPending: boolean;
  readonly now: number;
}): ActiveRedFlag | null {
  const { athlete, local, flagUploadPending, now } = input;
  if (athlete?.red_flag_active) {
    const kind = athlete.red_flag_kind ?? "advice";
    return {
      kind,
      severe: kind === "emergency" || athlete.red_flag_severity === "severe",
      note: athlete.red_flag_note,
      source: "server",
    };
  }
  if (athlete !== undefined && !flagUploadPending) return null;
  const stops = local
    .filter(
      (session) =>
        session.finished &&
        session.redFlag !== undefined &&
        typeof session.endedAt === "number" &&
        now - session.endedAt <= LOCAL_FLAG_WINDOW_MS,
    )
    .sort((a, b) => (b.endedAt ?? 0) - (a.endedAt ?? 0));
  const latest = stops[0];
  if (latest?.redFlag === undefined) return null;
  return {
    kind: latest.redFlag,
    severe: latest.redFlag === "emergency",
    note: null,
    source: "phone",
  };
}
