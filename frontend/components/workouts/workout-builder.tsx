"use client";

import { useMemo } from "react";

import { SectionLabel } from "@/components/design/section-label";
import { WorkoutProfileBars } from "@/components/design/workout-profile-bars";
import { EnduranceBuilder } from "@/components/workouts/endurance-builder";
import { StrengthBuilder } from "@/components/workouts/strength-builder";
import { formatDurationClock, formatSets } from "@/lib/format";
import {
  type StructureOutput,
  structureFromDraft,
  type WorkoutDraft,
} from "@/lib/workout-draft";
import { totalDurationS, totalSets } from "@/lib/workout-profile";

export interface WorkoutBuilderProps {
  readonly draft: WorkoutDraft;
  readonly onChange: (draft: WorkoutDraft) => void;
  /** Problems from `validateDraft`, or a 422 the API sent back. */
  readonly problems?: readonly string[];
}

/**
 * The prescription editor, whichever discipline it is for, with the profile
 * drawn from the draft as it is typed.
 *
 * The preview is not a second rendering path: it calls the same
 * `structureFromDraft` → `profileBars` chain the saved workout will go through,
 * so what the athlete sees while building is what the calendar will draw
 * afterwards. That is only possible because `profileBars` is pure and takes a
 * structure rather than a saved workout (slice 1).
 */
export function WorkoutBuilder({
  draft,
  onChange,
  problems = [],
}: WorkoutBuilderProps) {
  const structure = useMemo(
    // The generated `-Input` and `-Output` structures are the same document
    // under two names; the profile renderer is typed against the read side.
    () => structureFromDraft(draft) as StructureOutput,
    [draft],
  );
  const seconds = totalDurationS(structure);
  const sets = totalSets(structure);

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-3">
          <SectionLabel level={3}>Preview</SectionLabel>
          {/* Named, because the profile's own time axis below now renders the
              same total as its last tick and a test looking for the text
              alone would find two of them. */}
          <span
            data-slot="workout-total"
            className="font-mono text-ink-muted text-xs"
          >
            {draft.discipline === "cycling"
              ? seconds
                ? formatDurationClock(seconds)
                : "no duration yet"
              : sets
                ? formatSets(sets)
                : "no sets yet"}
          </span>
        </div>
        {draft.discipline === "cycling" ? (
          <WorkoutProfileBars structure={structure} size="detail" />
        ) : null}
        {draft.discipline === "cycling" && seconds === null ? (
          <p className="text-ink-faint text-xs">
            The bars are placeholders until the steps state their durations.
          </p>
        ) : null}
      </section>

      {problems.length > 0 ? (
        <ul
          role="alert"
          className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
        >
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}

      <section className="flex flex-col gap-2.5">
        <SectionLabel level={3}>
          {draft.discipline === "cycling" ? "Steps" : "Prescription"}
        </SectionLabel>
        {draft.discipline === "cycling" ? (
          <EnduranceBuilder
            steps={draft.steps}
            onChange={(steps) => onChange({ ...draft, steps })}
          />
        ) : (
          <StrengthBuilder
            groups={draft.groups}
            onChange={(groups) => onChange({ ...draft, groups })}
          />
        )}
      </section>
    </div>
  );
}
