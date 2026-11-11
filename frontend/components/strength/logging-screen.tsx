"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EffortRow } from "@/components/strength/effort-row";
import {
  type FinishChoice,
  FinishPanel,
} from "@/components/strength/finish-panel";
import {
  type Clock,
  primeRestSound,
  RestTimer,
  SYSTEM_CLOCK,
  WakeLockNotice,
} from "@/components/strength/rest-timer";
import { deviceId } from "@/components/strength/session-edits/device";
import {
  AddExerciseDialog,
  ContinueHereDialog,
  MoveSetsDialog,
  NoteDialog,
  ReorderDialog,
} from "@/components/strength/session-edits/dialogs";
import {
  addExercise,
  addedLine,
  addSet,
  linesOf,
  moveSets,
  placeNew,
  reorder,
} from "@/components/strength/session-edits/edits";
import {
  claimMutation,
  handedOver,
  heldElsewhere,
  takeOver,
} from "@/components/strength/session-edits/handoff";
import {
  abandonedEnd,
  finishMutation,
  mayReopen,
  recapSets,
  reopenMutation,
} from "@/components/strength/session-edits/lifecycle";
import {
  type LocalNotes,
  loadNotes,
  noteFor,
  saveNote,
} from "@/components/strength/session-edits/notes-store";
import {
  type ActiveSession,
  advance,
  allDone,
  applyRating,
  asksEffort,
  type Draft,
  deleteMutation,
  draftFor,
  type LoggedSet,
  lineComplete,
  nextOpenLine,
  type Packet,
  type PacketLine,
  type PacketSession,
  patchMutation,
  type Rating,
  ratingFields,
  ratingSnapshot,
  restFor,
  type Stamp,
  setMutation,
  startMutation,
  UNPLANNED,
  warmupsDone,
  workingSetsOf,
} from "@/components/strength/session-model";
import { loadSession, saveSession } from "@/components/strength/session-store";
import { Stepper } from "@/components/strength/stepper";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiClient } from "@/lib/api/client";
import { useI18n } from "@/lib/i18n/provider";
import type { Mutation } from "@/lib/offline/db";
import {
  activeSessions,
  enqueue,
  flush,
  subscribe,
  watchActiveSessions,
  withdraw,
} from "@/lib/offline/outbox";
import { refreshPacket, storedPacket } from "@/lib/offline/packet-store";
import { uuid7 } from "@/lib/offline/uuid7";
import {
  type Equipment,
  type Gym,
  kgToLb,
  lbToKg,
  loadableWeights,
  nearestLoadable,
  platesPerSide,
} from "@/lib/strength";
import { cn } from "@/lib/utils";

/**
 * `/train/[id]`: one strength session, logged set by set on one screen built
 * for one thumb, with or without signal.
 *
 * The layout is the direction's wireframe (§6.1): a header (End, the
 * exercise and its counter, ⋯), the warm-up strip with "✓ all", the logged
 * rows (tap to edit), the current set — weight and reps steppers, quick
 * buttons, last time, the reason chip, plates and the next set — and, pinned
 * to the bottom where the thumb is, the 72 px ✓. On an exercise's last working
 * set the ✓ becomes the effort row (`effort-row.tsx`); after the session's
 * last set it becomes Finish (`finish-panel.tsx`). The rest timer
 * (`rest-timer.tsx`) sits above the ✓ while it runs, not in its place, so
 * logging the next set early is still the one tap it is planned to be.
 *
 * **Offline first.** Every number comes from the session packet stored on
 * the phone (`lib/offline/packet-store.ts`); every action is written to the
 * outbox (`lib/offline/outbox.ts`) before anything else happens, and the
 * screen's own state is stored after every action (`session-store.ts`), so a
 * reload offers "Resume" at the next unlogged set with the rest timer
 * intact. Opening the screen for a session not yet started starts it — the
 * tap on Today's "Start" is the whole of starting.
 *
 * **Undo** takes the last action's mutations back out of the outbox for 6 s;
 * one that already uploaded is compensated (a delete, or the rating it
 * replaced) rather than left behind.
 *
 * **Changing the session on the fly** (`session-edits/`): the ⋯ menu
 * reorders exercises, adds a set or an exercise, moves mis-logged sets and
 * keeps a note per exercise; `/train/unplanned` is a session with no plan,
 * built from added exercises; `?recap=1` is "Log it now"; a finished session
 * reopens for 24 h, an abandoned one closes at its last set, and a second
 * phone opening an active session is asked "Continue here?".
 */

export const UNDO_MS = 6_000;

let lastWrite: Promise<unknown> = Promise.resolve();

/**
 * Resolves once every outbox write and state save queued so far has landed.
 * The writes outlive the screen on purpose — a set logged just before
 * leaving must still reach the disk — so a test waits on this before it
 * swaps in a fresh IndexedDB, or the last test's writes land in the next.
 */
export function writesSettled(): Promise<unknown> {
  return lastWrite;
}

type Phase =
  | "loading"
  | "missing"
  | "handoff"
  | "recap"
  | "resume"
  | "logging"
  | "finishing"
  | "done";

type EditDialog = "reorder" | "addExercise" | "move" | "note" | null;

interface UndoEntry {
  before: ActiveSession;
  writes: { mutationId: string; compensate: () => Mutation }[];
}

function gymOf(packet: Packet): Gym {
  const { gym } = packet;
  return {
    barKg: gym.bar_kg,
    platePairs: gym.plate_pairs.map((pair) => [pair.kg, pair.pairs] as const),
    dumbbellStepKg: gym.dumbbell_step_kg,
    dumbbellMaxKg: gym.dumbbell_max_kg,
    stackStepKg: gym.stack_step_kg,
    stackMaxKg: gym.stack_max_kg,
  };
}

/** Quick buttons, as one or two of the equipment's own steps. */
function quickDeltas(equipment: Equipment | null, gym: Gym): number[] {
  if (equipment === "dumbbell") {
    return [-2 * gym.dumbbellStepKg, -gym.dumbbellStepKg, gym.dumbbellStepKg];
  }
  if (equipment === "stack") {
    return [-2 * gym.stackStepKg, -gym.stackStepKg, gym.stackStepKg];
  }
  return [-5, -2.5, 2.5];
}

const FREE_STEP_KG = 2.5;

/**
 * The planned session id in the address bar, else `fallback`. Offline, the
 * service worker answers every `/train/<id>` with one cached page rendered
 * for `/train/shell` (`public/sw.js`), whose server-given id is "shell" —
 * the address bar is the only place that still names the session.
 */
function sessionIdFrom(fallback: string): string {
  const match = /^\/train\/([^/]+)\/?$/.exec(window.location.pathname);
  return match && match[1] !== "shell"
    ? decodeURIComponent(match[1])
    : fallback;
}

/** Whether the address asks for "Log it now" (`?recap=1`). */
function recapAsked(): boolean {
  return new URLSearchParams(window.location.search).get("recap") === "1";
}

export function LoggingScreen({
  plannedSessionId: givenId,
  clock = SYSTEM_CLOCK,
}: {
  plannedSessionId: string;
  clock?: Clock;
}) {
  const i18n = useI18n();
  const { t } = i18n;
  const [phase, setPhase] = useState<Phase>("loading");
  const [packet, setPacket] = useState<Packet | null>(null);
  const [planned, setPlanned] = useState<PacketSession | null>(null);
  const [session, setSession] = useState<ActiveSession | null>(null);
  const [undo, setUndo] = useState<UndoEntry | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState<LoggedSet | null>(null);
  const [menu, setMenu] = useState(false);
  const [dialog, setDialog] = useState<EditDialog>(null);
  const [me, setMe] = useState<string | null>(null);
  const [active, setActive] = useState(activeSessions);
  const [notes, setNotes] = useState<LocalNotes>({});
  const [candidate, setCandidate] = useState<NonNullable<
    ReturnType<typeof heldElsewhere>
  > | null>(null);
  const opened = useRef(false);
  // Outbox writes and state saves run one after another, in action order.
  const chain = useRef<Promise<unknown>>(Promise.resolve());

  const later = useCallback((work: () => Promise<unknown>) => {
    chain.current = chain.current.then(work).catch(() => undefined);
    lastWrite = chain.current;
    return chain.current;
  }, []);

  const stamp = useCallback((): Stamp => {
    const now = clock.wall();
    return {
      now,
      utcOffsetMin: -new Date(now).getTimezoneOffset(),
      id: () => uuid7(now),
    };
  }, [clock]);

  /** Show `next`, store it, and queue `mutations` — in that order on disk. */
  const commit = useCallback(
    (next: ActiveSession, mutations: Mutation[] = []) => {
      setSession(next);
      return later(async () => {
        for (const mutation of mutations) await enqueue(mutation);
        await saveSession(next);
      });
    },
    [later],
  );

  // While the screen is open every sync reads who holds which session, so a
  // phone that handed this one over turns read-only within one sync.
  useEffect(() => {
    const stop = watchActiveSessions();
    const unsubscribe = subscribe(() => setActive(activeSessions()));
    return () => {
      stop();
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    const plannedSessionId = sessionIdFrom(givenId);
    const unplanned = plannedSessionId === UNPLANNED;
    const find = async () => {
      const stored = await storedPacket().catch(() => null);
      return stored
        ? {
            packet: stored.packet,
            planned: stored.packet.sessions.find(
              (s) => s.planned_session_id === plannedSessionId,
            ),
          }
        : null;
    };
    void (async () => {
      const [device, localNotes] = await Promise.all([
        deviceId(clock.wall()),
        loadNotes(),
      ]);
      setMe(device);
      setNotes(localNotes);
      let found = await find();
      if (!found || (!found.planned && !unplanned)) {
        await refreshPacket().catch(() => undefined);
        found = await find();
      }
      if (!found || (!found.planned && !unplanned)) {
        setPhase("missing");
        return;
      }
      setPacket(found.packet);
      setPlanned(found.planned ?? null);
      const now = clock.wall();
      const existing = await loadSession(plannedSessionId);
      if (existing && !(unplanned && existing.finished)) {
        const end = abandonedEnd(existing, now);
        if (end !== null) {
          // Closed at its last set, as the server closes it.
          const closed = {
            ...existing,
            finished: true,
            endedAt: end,
            rest: null,
          };
          setSession(closed);
          setPhase("done");
          void commit(closed, [finishMutation(existing, end, stamp())]);
          return;
        }
        setSession(existing);
        setPhase(
          existing.finished
            ? "done"
            : existing.sets.length > 0
              ? "resume"
              : "logging",
        );
        return;
      }
      // Another phone may hold this session: ask the server, if it answers.
      if (!unplanned) {
        await flush().catch(() => undefined);
        const held = heldElsewhere(activeSessions(), plannedSessionId, device);
        if (held) {
          setCandidate(held);
          setPhase("handoff");
          return;
        }
      }
      const fresh: ActiveSession = {
        plannedSessionId,
        sessionClientId: uuid7(now),
        startedAt: now,
        sets: [],
        lineIndex: 0,
        rest: null,
        finished: false,
        ...(unplanned ? { lines: [] } : {}),
      };
      if (!unplanned && recapAsked()) {
        setSession(fresh);
        setPhase("recap");
        return;
      }
      setPhase("logging");
      void commit(fresh, [startMutation(fresh, stamp(), device)]);
    })();
  }, [givenId, clock, commit, stamp]);

  // The undo offer lasts six seconds after the action it would undo.
  useEffect(() => {
    if (!undo) return;
    const timer = window.setTimeout(() => setUndo(null), UNDO_MS);
    return () => window.clearTimeout(timer);
  }, [undo]);

  const lines = session ? linesOf(session, planned) : [];
  const line: PacketLine | undefined = session
    ? lines[session.lineIndex]
    : undefined;
  const gym = useMemo(() => (packet ? gymOf(packet) : null), [packet]);
  const scale = packet?.effort.scale ?? "reps_left";
  const units = packet?.settings.units ?? "kg";

  // The current set's numbers open with the line's pre-fill or its last set,
  // and reset whenever the set they are for changes.
  const draftKey =
    session && line
      ? `${line.line_ref}:${workingSetsOf(session, line.line_ref).length}`
      : "";
  const [draftFor_, setDraftFor] = useState("");
  if (session && line && draftKey !== draftFor_) {
    setDraftFor(draftKey);
    setDraft(draftFor(session, line));
  }

  if (phase === "loading") {
    return <Centered>{t("train.loading")}</Centered>;
  }
  if (phase === "handoff" && candidate && packet && planned) {
    const accept = () => {
      void (async () => {
        const { data } = await apiClient
          .GET("/api/v1/sessions/{session_id}", {
            params: { path: { session_id: candidate.session_id } },
          })
          .catch(() => ({ data: undefined }));
        if (!data || !me) {
          setPhase("missing");
          return;
        }
        const taken = takeOver(data, candidate, planned, packet);
        setCandidate(null);
        setPhase(taken.sets.length > 0 ? "logging" : "logging");
        await commit(taken, [claimMutation(taken, me, stamp())]);
        void flush();
      })();
    };
    return (
      <Centered>
        <ContinueHereDialog
          onContinue={accept}
          onDecline={() => {
            window.location.assign("/today");
          }}
        />
      </Centered>
    );
  }
  if (
    phase === "missing" ||
    !packet ||
    (!planned && session?.plannedSessionId !== UNPLANNED) ||
    !session ||
    !gym
  ) {
    return (
      <Centered>
        <p>{t("train.missing")}</p>
        <BackToToday />
      </Centered>
    );
  }

  const done = lines.length > 0 && allDone(session, lines);
  const readOnly = handedOver(active, session.sessionClientId, me);
  const note = line ? noteFor(line.exercise_id, packet, notes) : null;
  const weightText = (kg: number | null) =>
    kg === null
      ? t("train.bodyweight")
      : units === "lb"
        ? i18n.weight(Math.round(kgToLb(kg) * 10) / 10, "lb")
        : i18n.weight(kg);
  const summary = (set: Pick<Draft, "loadKg" | "reps" | "durationS">) =>
    set.durationS !== null
      ? set.loadKg === null
        ? t("train.secondsValue", { seconds: String(set.durationS) })
        : `${weightText(set.loadKg)} × ${set.durationS} s`
      : `${weightText(set.loadKg)} × ${set.reps ?? 0}`;

  // --- actions --------------------------------------------------------------

  const logCurrent = (rating: Rating | null) => {
    if (!line || !draft) return;
    const st = stamp();
    const writes: UndoEntry["writes"] = [];
    const mutations: Mutation[] = [];
    let created: LoggedSet = {
      clientId: uuid7(st.now),
      lineRef: line.line_ref,
      exerciseId: line.exercise_id,
      warmup: false,
      loadKg: draft.loadKg,
      reps: draft.durationS !== null ? null : draft.reps,
      durationS: draft.durationS,
      perSide: line.prescription.per_side === true,
      rir: null,
      rirCapped: false,
      effortSimple: null,
      completedAt: st.now,
    };
    let sets = session.sets;
    const patches: Mutation[] = [];
    if (rating) {
      sets = sets.map((set) => {
        if (set.lineRef !== line.line_ref || set.warmup) return set;
        const previous = ratingSnapshot(set, scale);
        const patch = patchMutation(
          session,
          set.clientId,
          ratingFields(rating, scale),
          st,
        );
        patches.push(patch);
        writes.push({
          mutationId: patch.mutation_id,
          compensate: () =>
            patchMutation(session, set.clientId, previous, stamp()),
        });
        return applyRating(set, rating, scale);
      });
      created = applyRating(created, rating, scale);
    }
    const placed = placeNew({ ...session, sets }, lines, [created], st);
    const create = setMutation(
      session,
      created,
      placed.indexOf(created.clientId),
      scale,
      st,
    );
    mutations.push(create, ...placed.shifts, ...patches);
    writes.unshift({
      mutationId: create.mutation_id,
      compensate: () => deleteMutation(session, created.clientId, stamp()),
    });
    let next = advance({ ...session, sets: placed.sets }, lines);
    next = {
      ...next,
      rest: allDone(next, lines)
        ? null
        : { endsAt: st.now + restFor(line) * 1000 },
    };
    primeRestSound();
    setUndo({ before: session, writes });
    void commit(next, mutations);
  };

  const logWarmups = () => {
    if (!line) return;
    const st = stamp();
    const from = warmupsDone(session, line.line_ref);
    const created = line.warmups.slice(from).map(
      (warmup): LoggedSet => ({
        clientId: uuid7(st.now),
        lineRef: line.line_ref,
        exerciseId: line.exercise_id,
        warmup: true,
        loadKg: warmup.kg,
        reps: warmup.reps,
        durationS: null,
        perSide: false,
        rir: null,
        rirCapped: false,
        effortSimple: null,
        completedAt: st.now,
      }),
    );
    if (created.length === 0) return;
    const placed = placeNew(session, lines, created, st);
    const creates = created.map((set) =>
      setMutation(session, set, placed.indexOf(set.clientId), scale, st),
    );
    const mutations = [...creates, ...placed.shifts];
    setUndo({
      before: session,
      writes: creates.map((mutation, i) => ({
        mutationId: mutation.mutation_id,
        compensate: () => deleteMutation(session, created[i].clientId, stamp()),
      })),
    });
    void commit({ ...session, sets: placed.sets }, mutations);
  };

  const undoLast = () => {
    if (!undo) return;
    const entry = undo;
    setUndo(null);
    setSession(entry.before);
    void later(async () => {
      const uploaded = new Set(
        await withdraw(entry.writes.map((write) => write.mutationId)),
      );
      for (const write of entry.writes) {
        if (uploaded.has(write.mutationId)) await enqueue(write.compensate());
      }
      await saveSession(entry.before);
    });
  };

  const saveEdit = (set: LoggedSet, changes: Draft | null) => {
    setEditing(null);
    const st = stamp();
    if (changes === null) {
      void commit(
        {
          ...session,
          sets: session.sets.filter((s) => s.clientId !== set.clientId),
        },
        [deleteMutation(session, set.clientId, st)],
      );
      return;
    }
    const patch =
      set.durationS !== null
        ? {
            load_kg: changes.loadKg,
            duration_s: changes.durationS ?? set.durationS,
          }
        : { load_kg: changes.loadKg, reps: changes.reps ?? set.reps ?? 1 };
    void commit(
      {
        ...session,
        sets: session.sets.map((s) =>
          s.clientId === set.clientId
            ? {
                ...s,
                loadKg: changes.loadKg,
                reps: set.durationS !== null ? null : (changes.reps ?? s.reps),
                durationS:
                  set.durationS !== null
                    ? (changes.durationS ?? s.durationS)
                    : null,
              }
            : s,
        ),
      },
      [patchMutation(session, set.clientId, patch, st)],
    );
  };

  const finish = (choice: FinishChoice) => {
    const st = stamp();
    const base = (id: string) => ({
      mutation_id: id,
      client_edit_time: new Date(st.now).toISOString(),
      session_client_id: session.sessionClientId,
    });
    const mutations: Mutation[] = [];
    if (choice.rating !== null) {
      mutations.push({
        kind: "session_rating",
        ...base(st.id()),
        session_rating: choice.rating,
      });
    }
    if (choice.dontLearn) {
      mutations.push({
        kind: "dont_learn",
        ...base(st.id()),
        dont_learn: true,
        ...(choice.reason ? { reason: choice.reason } : {}),
      });
    }
    mutations.push(finishMutation(session, st.now, st));
    setUndo(null);
    setPhase("done");
    void commit(
      { ...session, finished: true, rest: null, endedAt: st.now },
      mutations,
    ).then(() => flush());
  };

  const reopen = () => {
    const st = stamp();
    setPhase(session.sets.length > 0 ? "resume" : "logging");
    void commit({ ...session, finished: false, endedAt: null }, [
      reopenMutation(session, st),
    ]);
  };

  const logRecap = () => {
    const st = stamp();
    const created = recapSets(session, lines, () => uuid7(st.now));
    const started = {
      ...session,
      sets: created,
      finished: true,
      endedAt: st.now,
    };
    const mutations: Mutation[] = [
      startMutation(session, st, me ?? undefined),
      ...created.map((set, i) => setMutation(session, set, i, scale, st)),
      finishMutation(session, st.now, st),
    ];
    setPhase("done");
    void commit(started, mutations).then(() => flush());
  };

  const logSetBySet = () => {
    setPhase("logging");
    void commit(session, [startMutation(session, stamp(), me ?? undefined)]);
  };

  const edit = (next: ActiveSession, mutations: Mutation[] = []) => {
    setMenu(false);
    setUndo(null);
    void commit(next, mutations);
  };

  const saveNoteText = (text: string) => {
    if (!line) return;
    setDialog(null);
    const st = stamp();
    const exerciseId = line.exercise_id;
    setNotes((now) => ({ ...now, [exerciseId]: { text, savedAt: st.now } }));
    void later(async () => {
      await enqueue({
        kind: "note_upsert",
        mutation_id: st.id(),
        client_edit_time: new Date(st.now).toISOString(),
        exercise_id: exerciseId,
        text,
      });
      await saveNote(exerciseId, text, st.now);
    });
  };

  const moveLine = (direction: 1 | -1) => {
    setMenu(false);
    const count = lines.length;
    if (count === 0) return;
    const target = (session.lineIndex + direction + count) % count;
    void commit({ ...session, lineIndex: target });
  };

  // --- views ----------------------------------------------------------------

  if (phase === "done") {
    return (
      <Centered>
        <h1 className="font-semibold text-2xl">{t("train.done")}</h1>
        <p className="text-ink-muted">
          {t("train.doneBody", {
            count: String(session.sets.filter((s) => !s.warmup).length),
          })}
        </p>
        {mayReopen(session, clock.wall()) ? (
          <Button variant="outline" className="h-[48px]" onClick={reopen}>
            {t("edits.reopen")}
          </Button>
        ) : null}
        <BackToToday />
      </Centered>
    );
  }

  if (phase === "recap") {
    return (
      <Centered>
        <h1 className="font-semibold text-2xl">{t("edits.recapTitle")}</h1>
        <p className="text-ink-muted">{t("edits.recapBody")}</p>
        <ul className="flex w-full max-w-sm flex-col gap-1 text-left">
          {lines.map((one) => (
            <li
              key={one.line_ref}
              className="flex items-baseline justify-between gap-3 text-sm"
            >
              <span className="text-ink-secondary">{one.exercise_name}</span>
              <span className="font-mono text-ink text-xs">
                {summary(draftFor({ ...session, sets: [] }, one))} ×{" "}
                {one.prescription.sets}
              </span>
            </li>
          ))}
        </ul>
        <Button
          className="h-[64px] w-full max-w-sm text-base"
          aria-label={t("edits.recapAllLabel")}
          onClick={logRecap}
        >
          {t("edits.recapAll")}
        </Button>
        <Button variant="ghost" className="h-[44px]" onClick={logSetBySet}>
          {t("edits.recapSetBySet")}
        </Button>
      </Centered>
    );
  }

  if (phase === "resume") {
    const open = nextOpenLine(session, lines, session.lineIndex);
    const resumeLine = open === null ? null : lines[open];
    return (
      <Centered>
        <h1 className="font-semibold text-2xl">{t("train.resumeTitle")}</h1>
        <Button
          className="h-[64px] w-full max-w-sm text-base"
          onClick={() => setPhase("logging")}
        >
          {resumeLine
            ? t("train.resume", {
                n: String(
                  workingSetsOf(session, resumeLine.line_ref).length + 1,
                ),
                exercise: resumeLine.exercise_name,
              })
            : t("train.resumeFinish")}
        </Button>
      </Centered>
    );
  }

  const counter = {
    current: String(session.lineIndex + 1),
    total: String(lines.length),
  };

  return (
    // Exactly one screen tall, scrolling inside: the document around it grows
    // with its content (`app/layout.tsx`), and the ✓ has to stay at the
    // bottom of the glass, not at the bottom of a page the thumb scrolls.
    <div className="flex h-dvh min-h-0 shrink-0 flex-col bg-canvas">
      <header className="flex shrink-0 items-center gap-2 border-hairline border-b bg-chrome px-2 py-1.5">
        <Button
          variant="ghost"
          className="h-[44px] min-w-[44px] px-2"
          aria-label={t("train.endLabel")}
          onClick={() => setPhase("finishing")}
        >
          ⏸ {t("train.end")}
        </Button>
        <h1 className="min-w-0 flex-1 truncate font-semibold text-lg">
          {line?.exercise_name ?? t("edits.unplannedTitle")}
        </h1>
        <span
          className="font-mono text-ink-muted text-sm"
          role="note"
          aria-label={t("train.counterLabel", counter)}
        >
          {t("train.counter", counter)}
        </span>
        {readOnly ? null : (
          <div className="relative">
            <Button
              variant="ghost"
              className="h-[44px] min-w-[44px] text-xl"
              aria-label={t("train.more")}
              aria-expanded={menu}
              aria-haspopup="menu"
              onClick={() => setMenu((open) => !open)}
            >
              ⋯
            </Button>
            {menu ? (
              <div
                role="menu"
                className="absolute top-12 right-0 z-30 flex w-56 flex-col rounded-lg border border-hairline-strong bg-raised p-1"
              >
                <button
                  type="button"
                  role="menuitem"
                  className="min-h-[44px] rounded-md px-3 text-left hover:bg-raised-hover"
                  onClick={() => moveLine(1)}
                >
                  {t("train.nextExercise")}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="min-h-[44px] rounded-md px-3 text-left hover:bg-raised-hover"
                  onClick={() => moveLine(-1)}
                >
                  {t("train.previousExercise")}
                </button>
                {(
                  [
                    ["reorder", "edits.reorder", lines.length > 1],
                    ["addSet", "edits.addSet", Boolean(line)],
                    ["addExercise", "edits.addExercise", true],
                    [
                      "move",
                      "edits.moveSets",
                      Boolean(line) &&
                        lines.length > 1 &&
                        session.sets.some((s) => s.lineRef === line?.line_ref),
                    ],
                    ["note", "edits.note", Boolean(line)],
                  ] as const
                )
                  .filter(([, , shown]) => shown)
                  .map(([action, label]) => (
                    <button
                      key={action}
                      type="button"
                      role="menuitem"
                      className="min-h-[44px] rounded-md px-3 text-left hover:bg-raised-hover"
                      onClick={() => {
                        setMenu(false);
                        if (action === "addSet") {
                          edit(addSet(session, lines, session.lineIndex));
                        } else {
                          setDialog(action);
                        }
                      }}
                    >
                      {t(label)}
                    </button>
                  ))}
              </div>
            ) : null}
          </div>
        )}
      </header>

      {phase === "finishing" ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <FinishPanel onFinish={finish} onBack={() => setPhase("logging")} />
        </div>
      ) : (
        <>
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-3">
            <WakeLockNotice />
            {readOnly ? (
              <p
                role="status"
                className="rounded-lg border border-hairline-strong bg-raised px-3 py-2 text-ink-secondary"
              >
                {t("edits.readOnly")}
              </p>
            ) : null}
            {note ? (
              <section
                aria-label={t("edits.noteShown")}
                className="rounded-lg border border-hairline bg-raised px-3 py-2 text-ink-secondary text-sm"
              >
                {note}
              </section>
            ) : null}
            {!line ? (
              <div className="flex flex-col items-start gap-3">
                <p className="text-ink-muted">{t("edits.unplannedEmpty")}</p>
                <Button
                  className="h-[56px]"
                  onClick={() => setDialog("addExercise")}
                >
                  {t("edits.addExercise")}
                </Button>
              </div>
            ) : null}
            {line ? (
              <LineBody
                line={line}
                session={session}
                packet={packet}
                gym={gym}
                draft={draft}
                setDraft={setDraft}
                done={done}
                weightText={weightText}
                summary={summary}
                onWarmups={logWarmups}
                onEdit={setEditing}
                units={units}
                lines={lines}
              />
            ) : null}
          </div>

          <div className="flex shrink-0 flex-col gap-2 border-hairline border-t bg-chrome px-4 pt-2 pb-[max(env(safe-area-inset-bottom),0.75rem)]">
            {session.rest && !done ? (
              <RestTimer
                endsAt={session.rest.endsAt}
                clock={clock}
                onChange={(endsAt) =>
                  void commit({ ...session, rest: { endsAt } })
                }
                onSkip={() => void commit({ ...session, rest: null })}
              />
            ) : null}
            {undo ? (
              <Button
                variant="outline"
                className="h-[44px]"
                aria-label={t("train.undoLabel")}
                onClick={undoLast}
              >
                {t("train.undo")}
              </Button>
            ) : null}
            {readOnly ? null : done ? (
              <Button
                className="h-[72px] text-xl"
                onClick={() => setPhase("finishing")}
              >
                {t("train.finish")}
              </Button>
            ) : line && draft && asksEffort(packet, session, line) ? (
              <EffortRow
                scale={scale}
                onRate={(rating) => logCurrent(rating)}
              />
            ) : line && draft ? (
              <Button
                className="h-[72px] text-3xl"
                aria-label={t("train.logLabel", {
                  n: String(workingSetsOf(session, line.line_ref).length + 1),
                  summary: summary(draft),
                })}
                onClick={() => logCurrent(null)}
              >
                {t("train.log")}
              </Button>
            ) : null}
          </div>
        </>
      )}

      {dialog === "reorder" ? (
        <ReorderDialog
          lines={lines}
          onMove={(from, to) => {
            const result = reorder(session, lines, from, to, stamp());
            edit(result.session, result.mutations);
          }}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === "addExercise" ? (
        <AddExerciseDialog
          packet={packet}
          onPick={(exercise) => {
            setDialog(null);
            const now = clock.wall();
            edit(
              addExercise(
                session,
                lines,
                addedLine(packet, exercise, uuid7(now)),
              ),
            );
          }}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === "move" && line ? (
        <MoveSetsDialog
          sets={session.sets.filter((s) => s.lineRef === line.line_ref)}
          lines={lines}
          current={session.lineIndex}
          summary={summary}
          onMove={(ids, target) => {
            setDialog(null);
            const result = moveSets(session, lines, ids, target, stamp());
            edit(result.session, result.mutations);
          }}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === "note" && line ? (
        <NoteDialog
          exercise={line.exercise_name}
          text={note ?? ""}
          onSave={saveNoteText}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {editing ? (
        <EditSet
          set={editing}
          n={
            workingSetsOf(session, editing.lineRef).findIndex(
              (s) => s.clientId === editing.clientId,
            ) + 1
          }
          units={units}
          onSave={(changes) => saveEdit(editing, changes)}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 py-10 text-center">
      {children}
    </div>
  );
}

function BackToToday() {
  const { t } = useI18n();
  return (
    <Link
      href="/today"
      className="inline-flex min-h-[44px] items-center rounded-lg border border-hairline-strong px-4 text-ink"
    >
      {t("train.backToday")}
    </Link>
  );
}

function LineBody({
  line,
  session,
  packet,
  gym,
  draft,
  setDraft,
  done,
  weightText,
  summary,
  onWarmups,
  onEdit,
  units,
  lines,
}: {
  lines: readonly PacketLine[];
  line: PacketLine;
  session: ActiveSession;
  packet: Packet;
  gym: Gym;
  draft: Draft | null;
  setDraft: (draft: Draft) => void;
  done: boolean;
  weightText: (kg: number | null) => string;
  summary: (set: Pick<Draft, "loadKg" | "reps" | "durationS">) => string;
  onWarmups: () => void;
  onEdit: (set: LoggedSet) => void;
  units: "kg" | "lb";
}) {
  const { t } = useI18n();
  const working = workingSetsOf(session, line.line_ref);
  const warmed = warmupsDone(session, line.line_ref);
  const total = line.prescription.sets;
  const equipment = line.equipment ?? null;
  const weights = useMemo(
    () => (equipment ? loadableWeights(equipment, gym) : null),
    [equipment, gym],
  );
  const last = packet.last_numbers.find(
    (n) => n.exercise_id === line.exercise_id,
  );
  const timed = draft?.durationS !== null && draft?.durationS !== undefined;
  // Logged rows grow above the current set; keep the set in view as they do,
  // so at 320 px the numbers about to be logged are never scrolled away.
  const current = useRef<HTMLElement>(null);
  const position = `${line.line_ref}:${working.length}`;
  useEffect(() => {
    if (position) current.current?.scrollIntoView?.({ block: "nearest" });
  }, [position]);

  const stepWeight = (direction: 1 | -1) => {
    if (!draft) return;
    const current = draft.loadKg ?? 0;
    let next: number;
    if (weights) {
      const candidates =
        direction > 0
          ? weights.filter((w) => w > current)
          : weights.filter((w) => w < current).reverse();
      next = candidates[0] ?? current;
    } else {
      next = Math.max(0, current + direction * FREE_STEP_KG);
    }
    setDraft({ ...draft, loadKg: next === 0 && !weights ? null : next });
  };

  const quickTo = (delta: number): number | null => {
    if (!draft || draft.loadKg === null) return null;
    const target = draft.loadKg + delta;
    if (target < 0) return null;
    return equipment ? nearestLoadable(target, equipment, gym) : target;
  };

  const planned = draftFor({ ...session, sets: [] }, line);
  const reason =
    line.prefill.source === "prescription"
      ? t("train.reasonPlan")
      : line.prefill.source === "last_logged"
        ? t("train.reasonLast")
        : t("train.reasonBlank");
  const plates =
    equipment === "barbell" && draft?.loadKg
      ? platesPerSide(draft.loadKg, "barbell", gym)
      : null;

  const nextText = (() => {
    if (working.length + 1 < total && draft) {
      return t("train.nextSet", {
        n: String(working.length + 2),
        summary: summary(draft),
      });
    }
    for (let step = 1; step < lines.length; step++) {
      const other = lines[(session.lineIndex + step) % lines.length];
      if (!lineComplete(session, other)) return other.exercise_name;
    }
    return t("train.lastSet");
  })();

  return (
    <>
      {line.warmups.length > 0 ? (
        <div className="flex min-h-[44px] items-center gap-2 text-sm">
          <span className="text-ink-secondary">{t("train.warmup")}</span>
          <span aria-hidden className="flex gap-1">
            {line.warmups.map((warmup, i) => (
              <span
                key={`${warmup.kg}-${warmup.reps}`}
                className={cn(
                  "size-2.5 rounded-full border border-hairline-strong",
                  i < warmed && "bg-ink-secondary",
                )}
              />
            ))}
          </span>
          {warmed >= line.warmups.length ? (
            <span className="text-ink-muted">{t("train.warmupDone")}</span>
          ) : (
            <Button
              variant="outline"
              className="ml-auto h-[44px] min-w-[44px]"
              aria-label={t("train.warmupAllLabel")}
              onClick={onWarmups}
            >
              {t("train.warmupAll")}
            </Button>
          )}
        </div>
      ) : null}

      {working.length > 0 ? (
        <ol className="flex flex-col">
          {working.map((set, i) => (
            <li key={set.clientId}>
              <button
                type="button"
                className="flex min-h-[44px] w-full items-center gap-4 rounded-md px-1 text-left font-mono text-ink-secondary hover:bg-raised"
                aria-label={t("train.loggedRow", {
                  n: String(i + 1),
                  summary: summary(set),
                })}
                onClick={() => onEdit(set)}
              >
                <span className="w-5 text-ink-muted">{i + 1}</span>
                <span className="flex-1">{summary(set)}</span>
                <span aria-hidden>✓</span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}

      {!done && draft && working.length < total ? (
        <section
          ref={current}
          className="flex flex-col gap-2 border-hairline border-t pt-3"
        >
          <h2 className="font-mono text-ink-secondary text-sm uppercase tracking-wide">
            {t("train.setOf", {
              n: String(working.length + 1),
              total: String(total),
            })}
          </h2>
          <Stepper
            what={t("train.weight")}
            display={weightText(draft.loadKg)}
            value={
              draft.loadKg === null
                ? null
                : units === "lb"
                  ? Math.round(kgToLb(draft.loadKg) * 10) / 10
                  : draft.loadKg
            }
            decimal
            onStep={stepWeight}
            onEnter={(value) =>
              setDraft({
                ...draft,
                loadKg:
                  units === "lb"
                    ? Math.round(lbToKg(value) * 100) / 100
                    : value,
              })
            }
          />
          {timed ? (
            <Stepper
              what={t("train.seconds")}
              display={t("train.secondsValue", {
                seconds: String(draft.durationS),
              })}
              value={draft.durationS}
              onStep={(d) =>
                setDraft({
                  ...draft,
                  durationS: Math.max(1, (draft.durationS ?? 0) + d * 5),
                })
              }
              onEnter={(value) =>
                setDraft({
                  ...draft,
                  durationS: Math.max(1, Math.round(value)),
                })
              }
            />
          ) : (
            <Stepper
              what={t("train.reps")}
              display={t("train.repsValue", { reps: String(draft.reps ?? 0) })}
              value={draft.reps}
              onStep={(d) =>
                setDraft({ ...draft, reps: Math.max(1, (draft.reps ?? 0) + d) })
              }
              onEnter={(value) =>
                setDraft({ ...draft, reps: Math.max(1, Math.round(value)) })
              }
            />
          )}
          {draft.loadKg !== null ? (
            <div className="flex gap-1.5">
              {quickDeltas(equipment, gym)
                .slice(0, 2)
                .map((delta) => (
                  <QuickButton
                    key={delta}
                    delta={delta}
                    to={quickTo(delta)}
                    units={units}
                    current={draft.loadKg}
                    onPick={(kg) => setDraft({ ...draft, loadKg: kg })}
                  />
                ))}
              <Button
                variant="outline"
                className="h-[44px] min-w-0 flex-1"
                aria-label={t("train.sameLabel")}
                onClick={() => setDraft({ ...draft, loadKg: planned.loadKg })}
              >
                {t("train.same")}
              </Button>
              {quickDeltas(equipment, gym)
                .slice(2)
                .map((delta) => (
                  <QuickButton
                    key={delta}
                    delta={delta}
                    to={quickTo(delta)}
                    units={units}
                    current={draft.loadKg}
                    onPick={(kg) => setDraft({ ...draft, loadKg: kg })}
                  />
                ))}
            </div>
          ) : null}
          <div className="flex flex-col gap-1 text-sm">
            {last ? (
              <p className="font-mono text-ink-muted">
                {t("train.lastTime", {
                  summary: summary({
                    loadKg: last.load_kg,
                    reps: last.reps,
                    durationS: last.duration_s,
                  }),
                })}
              </p>
            ) : null}
            <p>
              <span className="inline-flex rounded-md border border-accent-border bg-accent-surface px-2 py-0.5 text-accent-quiet text-xs">
                {reason}
              </span>
            </p>
            {plates ? (
              <p className="font-mono text-ink-muted">
                {t("train.plates", {
                  plates:
                    plates.length > 0
                      ? plates.map((kg) => weightText(kg)).join(" · ")
                      : "—",
                })}{" "}
                · {t("train.bar", { weight: weightText(gym.barKg) })}
              </p>
            ) : null}
            <p className="font-mono text-ink-muted">
              {t("train.next", { summary: nextText })}
            </p>
          </div>
        </section>
      ) : null}
    </>
  );
}

function QuickButton({
  delta,
  to,
  current,
  units,
  onPick,
}: {
  delta: number;
  to: number | null;
  current: number | null;
  units: "kg" | "lb";
  onPick: (kg: number) => void;
}) {
  const { t, weight } = useI18n();
  const shown =
    units === "lb"
      ? Math.round(kgToLb(Math.abs(delta)) * 10) / 10
      : Math.abs(delta);
  const label = `${delta < 0 ? "−" : "+"}${weight(shown, units)}`;
  return (
    <Button
      variant="outline"
      className="h-[44px] min-w-0 flex-1 px-1 font-mono text-xs"
      aria-label={t("train.quickLabel", { delta: label })}
      disabled={to === null || to === current}
      onClick={() => {
        if (to !== null) onPick(to);
      }}
    >
      {label}
    </Button>
  );
}

function EditSet({
  set,
  n,
  units,
  onSave,
  onClose,
}: {
  set: LoggedSet;
  n: number;
  units: "kg" | "lb";
  /** null deletes the set. */
  onSave: (changes: Draft | null) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const toShown = (kg: number | null) =>
    kg === null
      ? ""
      : String(units === "lb" ? Math.round(kgToLb(kg) * 10) / 10 : kg);
  const [weight, setWeight] = useState(toShown(set.loadKg));
  const [count, setCount] = useState(String(set.durationS ?? set.reps ?? ""));
  const timed = set.durationS !== null;

  const save = () => {
    const shown = Number(weight.replace(",", "."));
    const loadKg =
      weight.trim() === "" || !Number.isFinite(shown) || shown < 0
        ? null
        : units === "lb"
          ? Math.round(lbToKg(shown) * 100) / 100
          : shown;
    const value = Math.max(1, Math.round(Number(count) || 1));
    onSave({
      loadKg,
      reps: timed ? null : value,
      durationS: timed ? value : null,
    });
  };

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="w-[min(24rem,calc(100vw-2rem))] gap-4 rounded-shell border border-hairline-card bg-panel p-5">
        <DialogHeader>
          <DialogTitle>{t("train.edit", { n: String(n) })}</DialogTitle>
        </DialogHeader>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-secondary">{t("train.weight")}</span>
          <input
            inputMode="decimal"
            className="h-[48px] rounded-lg border border-hairline-strong bg-inset px-3 font-mono text-lg text-ink"
            value={weight}
            onChange={(event) => setWeight(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-secondary">
            {timed ? t("train.seconds") : t("train.reps")}
          </span>
          <input
            inputMode="numeric"
            className="h-[48px] rounded-lg border border-hairline-strong bg-inset px-3 font-mono text-lg text-ink"
            value={count}
            onChange={(event) => setCount(event.target.value)}
          />
        </label>
        <DialogFooter className="flex-row gap-2">
          <Button
            variant="destructive"
            className="h-[44px]"
            onClick={() => onSave(null)}
          >
            {t("train.deleteSet")}
          </Button>
          <Button
            variant="ghost"
            className="ml-auto h-[44px]"
            onClick={onClose}
          >
            {t("train.cancel")}
          </Button>
          <Button className="h-[44px]" onClick={save}>
            {t("train.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
