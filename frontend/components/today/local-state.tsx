"use client";

import { useEffect, useState } from "react";

import type { ActiveSession } from "@/components/strength/session-model";
import { allTraining, queued } from "@/lib/offline/db";

/** What this phone knows about the sessions it has trained, and its outbox. */
export interface LocalState {
  /** Every session the logging screen has stored, any state. */
  readonly sessions: readonly ActiveSession[];
  /**
   * Whether a red-flag stop is still waiting in the outbox: the server does
   * not know about it yet (`lib/red-flag-state.ts`).
   */
  readonly flagUploadPending: boolean;
}

const NOTHING: LocalState = { sessions: [], flagUploadPending: false };

async function read(): Promise<LocalState> {
  try {
    const [sessions, waiting] = await Promise.all([
      allTraining<ActiveSession>(),
      queued(),
    ]);
    return {
      sessions,
      flagUploadPending: waiting.some(
        (item) => item.mutation.kind === "red_flag",
      ),
    };
  } catch {
    // No IndexedDB (a private window): the phone has no record to consult.
    return NOTHING;
  }
}

/**
 * The phone's own record of what was trained, read once on mount — `null`
 * until it has been read.
 *
 * Today asks it two things the server cannot answer in the gym: which of the
 * listed sessions is already under way or done on this phone (`Resume`, a
 * done state, `Reopen`), and whether a red-flag stop is still unuploaded.
 * Read once, not subscribed: Today is navigated to afresh after the gym
 * screen, and a stale read errs on the side of showing Start, which the red
 * flag gate and the gym screen's own resume both cover.
 */
export function useLocalState(): LocalState | null {
  const [state, setState] = useState<LocalState | null>(null);
  useEffect(() => {
    let live = true;
    void read().then((value) => {
      if (live) setState(value);
    });
    return () => {
      live = false;
    };
  }, []);
  return state;
}
