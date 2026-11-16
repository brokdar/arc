"use client";

import { useMemo, useState } from "react";

import type {
  LoggedSet,
  Packet,
  PacketLine,
} from "@/components/strength/session-model";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { components } from "@/generated/api/schema";
import { useI18n } from "@/lib/i18n/provider";

/**
 * The ⋯ menu's editing dialogs: reorder, add an exercise, move sets, note.
 * Each edits one thing about the session and hands the change back; the
 * logging screen turns it into state and outbox mutations
 * (`session-edits/edits.ts`). Centred modals, the tier for editing one record
 * (`.claude/rules/frontend-ui-conventions.md`).
 */

type Exercise = components["schemas"]["ExerciseRead"];

const PANEL =
  "w-[min(24rem,calc(100vw-2rem))] gap-4 rounded-shell border border-hairline-card bg-panel p-5";
const ROW =
  "flex min-h-[44px] items-center gap-2 rounded-md px-1 text-left hover:bg-raised";

/** Move exercises up and down; each tap applies at once. */
export function ReorderDialog({
  lines,
  onMove,
  onClose,
}: {
  lines: readonly PacketLine[];
  onMove: (from: number, to: number) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className={PANEL}>
        <DialogHeader>
          <DialogTitle>{t("edits.reorder")}</DialogTitle>
        </DialogHeader>
        <ol className="flex flex-col gap-1">
          {lines.map((line, i) => (
            <li key={line.line_ref} className={ROW}>
              <span className="w-5 font-mono text-ink-muted">{i + 1}</span>
              <span className="min-w-0 flex-1 truncate">
                {line.exercise_name}
              </span>
              <Button
                variant="outline"
                className="h-[44px] min-w-[44px]"
                aria-label={t("edits.moveUp", { exercise: line.exercise_name })}
                disabled={i === 0}
                onClick={() => onMove(i, i - 1)}
              >
                ↑
              </Button>
              <Button
                variant="outline"
                className="h-[44px] min-w-[44px]"
                aria-label={t("edits.moveDown", {
                  exercise: line.exercise_name,
                })}
                disabled={i === lines.length - 1}
                onClick={() => onMove(i, i + 1)}
              >
                ↓
              </Button>
            </li>
          ))}
        </ol>
        <DialogFooter>
          <Button className="h-[44px]" onClick={onClose}>
            {t("edits.done")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Pick an exercise from the packet's library, offline. */
export function AddExerciseDialog({
  packet,
  onPick,
  onClose,
}: {
  packet: Packet;
  onPick: (exercise: Exercise) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const wanted = query.trim().toLowerCase();
    return [...packet.library]
      .filter((exercise) => exercise.name.toLowerCase().includes(wanted))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [packet.library, query]);
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className={PANEL}>
        <DialogHeader>
          <DialogTitle>{t("edits.addExercise")}</DialogTitle>
        </DialogHeader>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-secondary">{t("edits.search")}</span>
          <input
            className="h-[48px] rounded-lg border border-hairline-strong bg-inset px-3 text-ink"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <ul className="flex max-h-[50dvh] flex-col overflow-y-auto">
          {shown.map((exercise) => (
            <li key={exercise.id}>
              <button
                type="button"
                className={`${ROW} w-full`}
                aria-label={t("edits.addThis", { exercise: exercise.name })}
                onClick={() => onPick(exercise)}
              >
                {exercise.name}
              </button>
            </li>
          ))}
        </ul>
        {shown.length === 0 ? (
          <p className="text-ink-muted text-sm">{t("edits.noMatch")}</p>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

/** Pick logged sets of the shown exercise and the exercise they belong to. */
export function MoveSetsDialog({
  sets,
  lines,
  current,
  summary,
  onMove,
  onClose,
}: {
  /** The shown exercise's logged sets, in order. */
  sets: readonly LoggedSet[];
  lines: readonly PacketLine[];
  current: number;
  summary: (set: LoggedSet) => string;
  onMove: (clientIds: string[], target: number) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [picked, setPicked] = useState<string[]>([]);
  const toggle = (id: string) =>
    setPicked((now) =>
      now.includes(id) ? now.filter((one) => one !== id) : [...now, id],
    );
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className={PANEL}>
        <DialogHeader>
          <DialogTitle>{t("edits.moveSets")}</DialogTitle>
        </DialogHeader>
        <p className="text-ink-muted text-sm">{t("edits.pickSets")}</p>
        <ul className="flex flex-col">
          {sets.map((set, i) => {
            const label = t("edits.setRow", {
              n: String(i + 1),
              summary: summary(set),
            });
            return (
              <li key={set.clientId}>
                <label className={`${ROW} font-mono`}>
                  <input
                    type="checkbox"
                    className="size-5"
                    aria-label={label}
                    checked={picked.includes(set.clientId)}
                    onChange={() => toggle(set.clientId)}
                  />
                  {label}
                </label>
              </li>
            );
          })}
        </ul>
        <div className="flex flex-col gap-1">
          {lines.map((line, i) =>
            i === current ? null : (
              <Button
                key={line.line_ref}
                variant="outline"
                className="h-[44px] justify-start"
                disabled={picked.length === 0}
                onClick={() => onMove(picked, i)}
              >
                {t("edits.moveTo", { exercise: line.exercise_name })}
              </Button>
            ),
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Write, change or clear the note on one exercise. */
export function NoteDialog({
  exercise,
  text,
  onSave,
  onClose,
}: {
  exercise: string;
  text: string;
  onSave: (text: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(text);
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className={PANEL}>
        <DialogHeader>
          <DialogTitle>{t("edits.noteTitle", { exercise })}</DialogTitle>
        </DialogHeader>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-secondary">{t("edits.note")}</span>
          <textarea
            rows={3}
            maxLength={2000}
            className="rounded-lg border border-hairline-strong bg-inset px-3 py-2 text-ink"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </label>
        <p className="text-ink-muted text-xs">{t("edits.noteHint")}</p>
        <DialogFooter className="flex-row gap-2">
          <Button
            variant="ghost"
            className="ml-auto h-[44px]"
            onClick={onClose}
          >
            {t("train.cancel")}
          </Button>
          <Button className="h-[44px]" onClick={() => onSave(value.trim())}>
            {t("train.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** "Continue here?" — another phone holds this session. */
export function ContinueHereDialog({
  onContinue,
  onDecline,
}: {
  onContinue: () => void;
  onDecline: () => void;
}) {
  const { t } = useI18n();
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDecline())}>
      <DialogContent className={PANEL}>
        <DialogHeader>
          <DialogTitle>{t("edits.continueTitle")}</DialogTitle>
        </DialogHeader>
        <p className="text-ink-secondary">{t("edits.continueBody")}</p>
        <DialogFooter className="flex-row gap-2">
          <Button
            variant="ghost"
            className="ml-auto h-[44px]"
            onClick={onDecline}
          >
            {t("edits.notNow")}
          </Button>
          <Button className="h-[44px]" onClick={onContinue}>
            {t("edits.continueHere")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
