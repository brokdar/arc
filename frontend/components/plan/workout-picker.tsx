"use client";

import { useId } from "react";

import { Field } from "@/components/design/field";
import { WorkoutProfileBars } from "@/components/design/workout-profile-bars";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { formatDurationHm, formatSets } from "@/lib/format";
import type { Discipline } from "@/lib/workout-draft";

type Workout = components["schemas"]["WorkoutRead"];

export interface WorkoutPickerProps {
  readonly discipline: Discipline;
  readonly value: string | null;
  readonly onChange: (workout: Workout | null) => void;
}

/**
 * Pick a prescription out of the library, with its profile.
 *
 * Filtered to the purpose's discipline, because a strength purpose on a bike
 * workout is a 422 the athlete should never have been able to compose. The
 * preview is the point: a name like "VO₂ 5×4′" is a label, and the bar profile
 * is the thing that says whether it is the session you meant.
 */
export function WorkoutPicker({
  discipline,
  value,
  onChange,
}: WorkoutPickerProps) {
  const base = useId();
  const workouts = $api.useQuery("get", "/api/v1/workouts", {
    params: { query: { discipline, limit: 100 } },
  });
  const items = workouts.data?.items ?? [];
  const selected = items.find((workout) => workout.id === value) ?? null;

  return (
    <div className="flex flex-col gap-2">
      <Field label="Workout" htmlFor={`${base}-workout`}>
        <NativeSelect
          className="w-full"
          id={`${base}-workout`}
          value={value ?? ""}
          onChange={(event) =>
            onChange(
              items.find((workout) => workout.id === event.target.value) ??
                null,
            )
          }
        >
          <NativeSelectOption value="">Choose a workout…</NativeSelectOption>
          {items.map((workout) => (
            <NativeSelectOption key={workout.id} value={workout.id}>
              {workout.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Field>

      {items.length === 0 && !workouts.isPending ? (
        <p className="text-ink-muted text-xs">
          No {discipline} workouts in the library yet — write one under
          Workouts, or describe the session inline instead.
        </p>
      ) : null}

      {selected ? (
        <div className="flex flex-col gap-1.5 rounded-button border border-hairline bg-inset px-3 py-2.5">
          <span className="flex items-center justify-between gap-2 font-mono text-ink-muted text-xs">
            <span>
              {selected.discipline === "cycling"
                ? formatDurationHm(selected.summary.total_duration_s)
                : formatSets(selected.summary.total_sets)}
            </span>
            <span className="text-ink-faint">
              {selected.discipline === "cycling"
                ? `${selected.summary.step_count} steps`
                : `${selected.structure.discipline === "strength" ? selected.structure.groups.length : 0} groups`}
            </span>
          </span>
          <WorkoutProfileBars structure={selected.structure} />
          {selected.description ? (
            <span className="text-ink-muted text-xs leading-snug">
              {selected.description}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
