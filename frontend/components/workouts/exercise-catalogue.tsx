"use client";

import { useId, useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";

type Exercise = components["schemas"]["ExerciseRead"];

/**
 * The whole movement catalogue, once.
 *
 * It is a static reference table of a few dozen rows that changes when the
 * application ships, not when the athlete trains — so every consumer shares
 * one query and react-query dedupes the request. The strength builder needs it
 * to offer names; the session sheet needs it to *print* them, instead of
 * prettifying `barbell-back-squat` into something that is right by luck.
 */
export function useExercises(): {
  exercises: Exercise[];
  nameOf: (exerciseId: string) => string;
} {
  const { data } = $api.useQuery("get", "/api/v1/exercises", {
    params: { query: { limit: 200 } },
  });
  const exercises = useMemo(() => data?.items ?? [], [data]);
  const names = useMemo(
    () => new Map(exercises.map((exercise) => [exercise.id, exercise.name])),
    [exercises],
  );
  return {
    exercises,
    // Until the catalogue arrives — and for a slug it does not hold — fall
    // back to the readable form of the slug rather than to a blank cell.
    nameOf: (exerciseId: string) =>
      names.get(exerciseId) ?? prettifySlug(exerciseId),
  };
}

export interface ExercisePickerProps {
  readonly value: string;
  readonly onChange: (exerciseId: string) => void;
  readonly id?: string;
  readonly className?: string;
}

/**
 * Pick a movement by typing its name.
 *
 * An `<input list>` over a `<datalist>` rather than a custom combobox: the
 * catalogue is short, the browser already knows how to filter and
 * keyboard-navigate a datalist, and the control degrades to a plain text field
 * everywhere that does not. The *name* is what the athlete types; the slug is
 * what the payload carries, and a name that matches nothing clears the value
 * so the draft never claims an exercise that is not in the catalogue.
 */
export function ExercisePicker({
  value,
  onChange,
  id,
  className,
}: ExercisePickerProps) {
  const listId = useId();
  const { exercises, nameOf } = useExercises();
  // `null` means "not typed in yet", so the field keeps showing the resolved
  // name — and starts showing it when the catalogue lands, which can be after
  // this row mounted with a slug from a saved workout. Once the athlete types,
  // what they typed wins, and no effect has to referee the two.
  const [typed, setTyped] = useState<string | null>(null);
  const text = typed ?? (value ? nameOf(value) : "");

  function handleChange(next: string) {
    setTyped(next);
    const match = exercises.find(
      (exercise) => exercise.name.toLowerCase() === next.trim().toLowerCase(),
    );
    onChange(match ? match.id : "");
  }

  return (
    <>
      <Input
        id={id}
        list={listId}
        className={className}
        placeholder="Search movements…"
        value={text}
        onChange={(event) => handleChange(event.target.value)}
      />
      <datalist id={listId}>
        {exercises.map((exercise) => (
          <option key={exercise.id} value={exercise.name}>
            {exercise.category}
          </option>
        ))}
      </datalist>
    </>
  );
}

/** `barbell-back-squat` → `Barbell back squat`. The catalogue's stand-in. */
function prettifySlug(slug: string): string {
  const words = slug.replace(/[-_]/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
