"use client";

import { useId } from "react";

import { Field, FieldRow } from "@/components/design/field";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { ExercisePicker } from "@/components/workouts/exercise-catalogue";
import {
  blankStrengthGroup,
  blankStrengthItem,
  type DraftStrengthGroup,
  type DraftStrengthItem,
  LOAD_KIND_LABELS,
  LOAD_KINDS,
  type LoadKind,
} from "@/lib/workout-draft";

export interface StrengthBuilderProps {
  readonly groups: readonly DraftStrengthGroup[];
  readonly onChange: (groups: readonly DraftStrengthGroup[]) => void;
}

/**
 * The strength prescription editor: groups of lines, each line a movement.
 *
 * A group with more than one line **is** a superset — there is no flag, in the
 * domain or here, because a flag and a count can disagree and a shape cannot.
 * So "make this a superset" is spelled "add a movement to this group", and the
 * group announces itself as one as soon as it holds two.
 */
export function StrengthBuilder({ groups, onChange }: StrengthBuilderProps) {
  return (
    <div className="flex flex-col gap-2.5">
      {groups.map((group, index) => (
        <GroupCard
          key={group.id}
          group={group}
          index={index}
          onChange={(next) =>
            onChange(groups.map((g) => (g.id === group.id ? next : g)))
          }
          onRemove={() => onChange(groups.filter((g) => g.id !== group.id))}
        />
      ))}
      <div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onChange([...groups, blankStrengthGroup()])}
        >
          Add exercise
        </Button>
      </div>
    </div>
  );
}

function GroupCard({
  group,
  index,
  onChange,
  onRemove,
}: {
  group: DraftStrengthGroup;
  index: number;
  onChange: (group: DraftStrengthGroup) => void;
  onRemove: () => void;
}) {
  const base = useId();
  const superset = group.items.length > 1;

  return (
    <div
      data-testid="draft-group"
      data-superset={superset ? "true" : undefined}
      className="flex flex-col gap-2.5 rounded-card border border-hairline-card bg-card px-3.5 py-3"
    >
      <div className="flex items-center gap-2">
        <SectionLabel className="mr-auto">
          {superset ? "Superset" : `Exercise ${index + 1}`}
        </SectionLabel>
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="text-ink-faint"
          aria-label="Remove group"
          onClick={onRemove}
        >
          Remove
        </Button>
      </div>

      <Field
        label="Group label"
        hint="optional"
        htmlFor={`${base}-label`}
        className="max-w-[220px]"
      >
        <Input
          id={`${base}-label`}
          className="h-7"
          placeholder={superset ? "Superset A" : ""}
          value={group.label}
          onChange={(event) =>
            onChange({ ...group, label: event.target.value })
          }
        />
      </Field>

      {group.items.map((item) => (
        <ItemRow
          key={item.id}
          item={item}
          removable={group.items.length > 1}
          onChange={(next) =>
            onChange({
              ...group,
              items: group.items.map((i) => (i.id === item.id ? next : i)),
            })
          }
          onRemove={() =>
            onChange({
              ...group,
              items: group.items.filter((i) => i.id !== item.id),
            })
          }
        />
      ))}

      <div>
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="text-ink-muted"
          onClick={() =>
            onChange({ ...group, items: [...group.items, blankStrengthItem()] })
          }
        >
          + Pair a movement (superset)
        </Button>
      </div>
    </div>
  );
}

function ItemRow({
  item,
  removable,
  onChange,
  onRemove,
}: {
  item: DraftStrengthItem;
  removable: boolean;
  onChange: (item: DraftStrengthItem) => void;
  onRemove: () => void;
}) {
  const base = useId();
  const bodyweight = item.loadKind === "bodyweight";
  const hold = item.mode === "hold";
  // What the athlete wrote is rounds; what everything downstream counts is
  // working sets. The hint says so at the point of entry rather than leaving
  // the difference to be discovered on the week rail.
  const workingSets = (Number(item.sets) || 0) * 2;

  return (
    <FieldRow
      data-testid="draft-set"
      className="rounded-button border border-hairline-faint bg-inset px-2.5 py-2"
    >
      <Field
        label="Movement"
        htmlFor={`${base}-exercise`}
        className="min-w-[170px] flex-1"
      >
        <ExercisePicker
          id={`${base}-exercise`}
          className="h-7"
          value={item.exerciseId}
          onChange={(exerciseId) => onChange({ ...item, exerciseId })}
        />
      </Field>

      <Field
        label="Rounds"
        hint={item.perSide ? `${workingSets} sets` : undefined}
        htmlFor={`${base}-sets`}
        className="w-[70px]"
      >
        <Input
          id={`${base}-sets`}
          inputMode="numeric"
          className="h-7 font-mono"
          value={item.sets}
          onChange={(event) => onChange({ ...item, sets: event.target.value })}
        />
      </Field>

      <Field label="Each" htmlFor={`${base}-mode`} className="w-[86px]">
        <NativeSelect
          className="w-full"
          size="sm"
          id={`${base}-mode`}
          value={item.mode}
          onChange={(event) =>
            // The other field is cleared on the way across: a line prescribes
            // reps or a hold, and a value left behind in the one not in use
            // would be sent as soon as the mode changed back.
            onChange(
              event.target.value === "hold"
                ? { ...item, mode: "hold", reps: "" }
                : { ...item, mode: "reps", durationS: "" },
            )
          }
        >
          <NativeSelectOption value="reps">Reps</NativeSelectOption>
          <NativeSelectOption value="hold">Hold</NativeSelectOption>
        </NativeSelect>
      </Field>

      {hold ? (
        <Field
          label="Seconds"
          htmlFor={`${base}-duration`}
          className="w-[68px]"
        >
          <Input
            id={`${base}-duration`}
            inputMode="numeric"
            className="h-7 font-mono"
            value={item.durationS}
            onChange={(event) =>
              onChange({ ...item, durationS: event.target.value })
            }
          />
        </Field>
      ) : (
        <Field label="Reps" htmlFor={`${base}-reps`} className="w-[58px]">
          <Input
            id={`${base}-reps`}
            inputMode="numeric"
            className="h-7 font-mono"
            value={item.reps}
            onChange={(event) =>
              onChange({ ...item, reps: event.target.value })
            }
          />
        </Field>
      )}

      <Field label="Per side" htmlFor={`${base}-per-side`} className="w-[74px]">
        <NativeSelect
          className="w-full"
          size="sm"
          id={`${base}-per-side`}
          value={item.perSide ? "yes" : "no"}
          onChange={(event) =>
            onChange({ ...item, perSide: event.target.value === "yes" })
          }
        >
          <NativeSelectOption value="no">Both</NativeSelectOption>
          <NativeSelectOption value="yes">Per side</NativeSelectOption>
        </NativeSelect>
      </Field>

      <Field label="Load" htmlFor={`${base}-load-kind`} className="w-[104px]">
        <NativeSelect
          className="w-full"
          size="sm"
          id={`${base}-load-kind`}
          value={item.loadKind}
          onChange={(event) =>
            onChange({
              ...item,
              loadKind: event.target.value as LoadKind,
              // A bodyweight load carries no value, and the domain says so —
              // clearing it here keeps the draft from smuggling one through.
              loadValue:
                event.target.value === "bodyweight" ? "" : item.loadValue,
            })
          }
        >
          {LOAD_KINDS.map((kind) => (
            <NativeSelectOption key={kind} value={kind}>
              {LOAD_KIND_LABELS[kind]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Field>

      {bodyweight ? null : (
        <Field
          label="Value"
          hint={LOAD_KIND_LABELS[item.loadKind]}
          htmlFor={`${base}-load-value`}
          className="w-[74px]"
        >
          <Input
            id={`${base}-load-value`}
            inputMode="decimal"
            className="h-7 font-mono"
            value={item.loadValue}
            onChange={(event) =>
              onChange({ ...item, loadValue: event.target.value })
            }
          />
        </Field>
      )}

      <Field label="RIR" htmlFor={`${base}-rir`} className="w-[54px]">
        <Input
          id={`${base}-rir`}
          inputMode="numeric"
          className="h-7 font-mono"
          value={item.rir}
          onChange={(event) => onChange({ ...item, rir: event.target.value })}
        />
      </Field>

      <Field
        label="Rest"
        hint="s"
        htmlFor={`${base}-rest`}
        className="w-[62px]"
      >
        <Input
          id={`${base}-rest`}
          inputMode="numeric"
          className="h-7 font-mono"
          value={item.restS}
          onChange={(event) => onChange({ ...item, restS: event.target.value })}
        />
      </Field>

      {removable ? (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="mb-0.5 text-ink-faint"
          aria-label="Remove movement"
          onClick={onRemove}
        >
          Remove
        </Button>
      ) : null}
    </FieldRow>
  );
}
