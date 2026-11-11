"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EffortRow } from "@/components/strength/effort-row";
import {
  type FinishChoice,
  FinishPanel,
} from "@/components/strength/finish-panel";
import { FinishSummary } from "@/components/strength/history/finish-summary";
import {
  type Clock,
  primeRestSound,
  RestTimer,
  SYSTEM_CLOCK,
  WakeLockNotice,
} from "@/components/strength/rest-timer";
import { PainPanel } from "@/components/strength/safety/pain-panel";
import {
  RedFlagPanel,
  RedFlagStop,
} from "@/components/strength/safety/red-flag";
import {
  type PainBand,
  type PainKind,
  RED_FLAG_GROUP,
  type RedFlagSymptom,
} from "@/components/strength/safety/safety-model";
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
  stoppedByPain,
  warmupsDone,
  workingSetsOf,
} from "@/components/strength/session-model";
import { loadSession, saveSession } from "@/components/strength/session-store";
import { Stepper } from "@/components/strength/stepper";
import { WhySheet } from "@/components/strength/why-sheet";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/lib/i18n/provider";
import type { Mutation } from "@/lib/offline/db";
import { enqueue, flush, withdraw } from "@/lib/offline/outbox";
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
  | "resume"
  | "logging"
  | "finishing"
  | "done";

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
  // The finish summary asks the server for the session's new bests, so it
  // waits until the outbox has drained: before that the server cannot know.
  const [uploaded, setUploaded] = useState(false);
  const [sheet, setSheet] = useState<"pain" | "redFlag" | null>(null);
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

  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    const plannedSessionId = sessionIdFrom(givenId);
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
      let found = await find();
      if (!found?.planned) {
        await refreshPacket().catch(() => undefined);
        found = await find();
      }
      if (!found?.planned) {
        setPhase("missing");
        return;
      }
      setPacket(found.packet);
      setPlanned(found.planned);
      const existing = await loadSession(plannedSessionId);
      if (existing) {
        setSession(existing);
        if (existing.finished) {
          void flush().then((outcome) => setUploaded(outcome === "drained"));
        }
        setPhase(
          existing.finished
            ? "done"
            : existing.sets.length > 0
              ? "resume"
              : "logging",
        );
        return;
      }
      const now = clock.wall();
      const fresh: ActiveSession = {
        plannedSessionId,
        sessionClientId: uuid7(now),
        startedAt: now,
        sets: [],
        lineIndex: 0,
        rest: null,
        finished: false,
      };
      setPhase("logging");
      void commit(fresh, [startMutation(fresh, stamp())]);
    })();
  }, [givenId, clock, commit, stamp]);

  // The undo offer lasts six seconds after the action it would undo.
  useEffect(() => {
    if (!undo) return;
    const timer = window.setTimeout(() => setUndo(null), UNDO_MS);
    return () => window.clearTimeout(timer);
  }, [undo]);

  const lines = planned?.lines ?? [];
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
  if (phase === "missing" || !packet || !planned || !session || !gym) {
    return (
      <Centered>
        <p>{t("train.missing")}</p>
        <BackToToday />
      </Centered>
    );
  }

  const done = allDone(session, lines);
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
    const create = setMutation(session, created, sets.length, scale, st);
    mutations.push(create, ...patches);
    writes.unshift({
      mutationId: create.mutation_id,
      compensate: () => deleteMutation(session, created.clientId, stamp()),
    });
    let next = advance({ ...session, sets: [...sets, created] }, lines);
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
      }),
    );
    if (created.length === 0) return;
    const mutations = created.map((set, i) =>
      setMutation(session, set, session.sets.length + i, scale, st),
    );
    setUndo({
      before: session,
      writes: mutations.map((mutation, i) => ({
        mutationId: mutation.mutation_id,
        compensate: () => deleteMutation(session, created[i].clientId, stamp()),
      })),
    });
    void commit({ ...session, sets: [...session.sets, ...created] }, mutations);
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
    mutations.push({
      kind: "session_finish",
      ...base(st.id()),
      end_time: new Date(st.now).toISOString(),
    });
    setUndo(null);
    setPhase("done");
    void commit({ ...session, finished: true, rest: null }, mutations)
      .then(() => flush())
      .then((outcome) => setUploaded(outcome === "drained"));
  };

  /** The set a pain note is about: the current exercise's last, else the last. */
  const painTarget = (() => {
    const working = session.sets.filter((s) => !s.warmup);
    const here = line ? working.filter((s) => s.lineRef === line.line_ref) : [];
    return here[here.length - 1] ?? working[working.length - 1] ?? null;
  })();

  const setPain = (
    target: LoggedSet,
    kind: PainKind | null,
    band: PainBand | null,
  ) => {
    const st = stamp();
    const previous = {
      pain_kind: target.painKind ?? null,
      pain_band: target.painBand ?? null,
    };
    const patch = patchMutation(
      session,
      target.clientId,
      { pain_kind: kind, pain_band: band },
      st,
    );
    let next: ActiveSession = {
      ...session,
      sets: session.sets.map((s) =>
        s.clientId === target.clientId
          ? { ...s, painKind: kind, painBand: band }
          : s,
      ),
    };
    if (kind === null) {
      // The sets come back: put the exercise they belong to in front again.
      const index = lines.findIndex((l) => l.line_ref === target.lineRef);
      if (index >= 0) next = { ...next, lineIndex: index };
    } else {
      next = advance(next, lines);
      if (allDone(next, lines)) next = { ...next, rest: null };
    }
    setUndo({
      before: session,
      writes: [
        {
          mutationId: patch.mutation_id,
          compensate: () =>
            patchMutation(session, target.clientId, previous, stamp()),
        },
      ],
    });
    void commit(next, [patch]);
  };

  const reportPain = (kind: PainKind, band: PainBand) => {
    setSheet(null);
    if (painTarget) setPain(painTarget, kind, band);
  };

  /** One tap takes back the sharp-pain note that stopped `stopped`. */
  const undoPain = (stopped: PacketLine) => {
    const target = session.sets.find(
      (s) =>
        s.lineRef === stopped.line_ref && !s.warmup && s.painKind === "sharp",
    );
    if (target) setPain(target, null, null);
  };

  const stopForRedFlag = (symptom: RedFlagSymptom) => {
    setSheet(null);
    const st = stamp();
    const base = (id: string) => ({
      mutation_id: id,
      client_edit_time: new Date(st.now).toISOString(),
      session_client_id: session.sessionClientId,
    });
    // The flag first: the server raises it before the session ends.
    const mutations: Mutation[] = [
      { kind: "red_flag", ...base(st.id()), symptom },
      {
        kind: "session_finish",
        ...base(st.id()),
        end_time: new Date(st.now).toISOString(),
      },
    ];
    setUndo(null);
    setPhase("done");
    void commit(
      {
        ...session,
        finished: true,
        rest: null,
        redFlag: RED_FLAG_GROUP[symptom],
      },
      mutations,
    ).then(() => flush());
  };

  const moveLine = (direction: 1 | -1) => {
    setMenu(false);
    const count = lines.length;
    const target = (session.lineIndex + direction + count) % count;
    void commit({ ...session, lineIndex: target });
  };

  // --- views ----------------------------------------------------------------

  if (phase === "done" && session.redFlag) {
    return (
      <Centered>
        <RedFlagStop kind={session.redFlag} />
        <BackToToday />
      </Centered>
    );
  }

  if (phase === "done") {
    return (
      <Centered>
        <h1 className="font-semibold text-2xl">{t("train.done")}</h1>
        <p className="text-ink-muted">
          {t("train.doneBody", {
            count: String(session.sets.filter((s) => !s.warmup).length),
          })}
        </p>
        {uploaded ? (
          <FinishSummary
            sessionClientId={session.sessionClientId}
            units={units}
          />
        ) : null}
        <BackToToday />
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
          {line?.exercise_name}
        </h1>
        <span
          className="font-mono text-ink-muted text-sm"
          role="note"
          aria-label={t("train.counterLabel", counter)}
        >
          {t("train.counter", counter)}
        </span>
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
              <button
                type="button"
                role="menuitem"
                disabled={painTarget === null}
                className="min-h-[44px] rounded-md px-3 text-left hover:bg-raised-hover disabled:opacity-50"
                onClick={() => {
                  setMenu(false);
                  setSheet("pain");
                }}
              >
                {t("safety.menuPain")}
              </button>
              <button
                type="button"
                role="menuitem"
                className="min-h-[44px] rounded-md px-3 text-left text-destructive hover:bg-raised-hover"
                onClick={() => {
                  setMenu(false);
                  setSheet("redFlag");
                }}
              >
                {t("safety.menuStop")}
              </button>
            </div>
          ) : null}
        </div>
      </header>

      {phase === "finishing" ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <FinishPanel onFinish={finish} onBack={() => setPhase("logging")} />
        </div>
      ) : (
        <>
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-3">
            <WakeLockNotice />
            {lines
              .filter((l) => stoppedByPain(session, l))
              .map((l) => (
                <div
                  key={l.line_ref}
                  role="status"
                  className="flex items-center gap-2 rounded-lg border border-hairline-strong bg-raised px-3 py-2 text-sm"
                >
                  <p className="min-w-0 flex-1">
                    {t("safety.pain.stopped", { exercise: l.exercise_name })}
                  </p>
                  <Button
                    variant="outline"
                    className="h-[44px]"
                    aria-label={t("safety.pain.undoLabel", {
                      exercise: l.exercise_name,
                    })}
                    onClick={() => undoPain(l)}
                  >
                    {t("safety.pain.undo")}
                  </Button>
                </div>
              ))}
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
            {done ? (
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

      {sheet === "pain" ? (
        <PainPanel onReport={reportPain} onClose={() => setSheet(null)} />
      ) : null}
      {sheet === "redFlag" ? (
        <RedFlagPanel
          onChoose={stopForRedFlag}
          onClose={() => setSheet(null)}
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
  // Without a suggestion (Plain logbook, follow-exactly, a line with no
  // loadable weight) the chip says where the pre-fill came from.
  const reason =
    line.prefill.source === "prescription"
      ? t("train.reasonPlan")
      : line.prefill.source === "blank"
        ? t("train.reasonBlank")
        : t("train.reasonLast");
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
            <WhySheet
              suggestion={line.suggestion}
              fallback={reason}
              weightText={weightText}
            />
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
