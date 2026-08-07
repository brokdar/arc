"use client";

import { useId, useState } from "react";

import { Field, FieldRow } from "@/components/design/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { components } from "@/generated/api/schema";
import {
  blankCriterion,
  CRITERION_KIND_LABELS,
  type CriterionKind,
  criterionKindsFor,
  describeCriterion,
  type SuccessCriterion,
} from "@/lib/criteria";
import {
  formatDurationClock,
  parseDurationInput,
  parseNumberInput,
} from "@/lib/format";

type Schemas = components["schemas"];
type Channel = Schemas["Channel"];
type StepRole = Schemas["StepRole"];

const CHANNEL_LABELS: Readonly<Record<Channel, string>> = {
  power: "Power",
  hr: "Heart rate",
  cadence: "Cadence",
};

const ROLE_LABELS: Readonly<Record<StepRole, string>> = {
  warmup: "Warm-up",
  work: "Work",
  recovery: "Recovery",
  rest: "Rest",
  cooldown: "Cool-down",
};

export interface CriteriaEditorProps {
  readonly criteria: readonly SuccessCriterion[];
  readonly onChange: (criteria: readonly SuccessCriterion[]) => void;
  readonly discipline: Schemas["Discipline"];
  /** Shown when the list is still the purpose template's, untouched. */
  readonly fromTemplate?: boolean;
  readonly onResetToTemplate?: () => void;
}

/**
 * The success-criteria editor.
 *
 * Every criterion is shown as the sentence `describeCriterion` produces —
 * exactly the sentence the calendar sheet and the Today view will show — with
 * its numbers underneath as fields. The wire format is a tagged union chosen
 * to be *evaluable*, and reading it back to the athlete in its own terms
 * ("min_fraction 0.75, selector role work") would make the editor a JSON form.
 *
 * Which kinds are on offer depends on the discipline: `sets_completed` refers
 * to something a ride does not have, and the domain refuses it.
 */
export function CriteriaEditor({
  criteria,
  onChange,
  discipline,
  fromTemplate = false,
  onResetToTemplate,
}: CriteriaEditorProps) {
  const base = useId();
  const kinds = criterionKindsFor(discipline);
  const [kind, setKind] = useState<CriterionKind>(kinds[0] ?? "duration_floor");

  const replace = (index: number, next: SuccessCriterion) =>
    onChange(criteria.map((criterion, i) => (i === index ? next : criterion)));

  return (
    <div className="flex flex-col gap-2">
      {fromTemplate && criteria.length > 0 ? (
        <p className="text-ink-faint text-2xs">
          From this purpose's template. Edit any of them and they stop following
          the purpose.
        </p>
      ) : null}
      {!fromTemplate && onResetToTemplate ? (
        <div>
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="text-ink-muted"
            onClick={onResetToTemplate}
          >
            Reset to the purpose's template
          </Button>
        </div>
      ) : null}

      {criteria.length === 0 ? (
        <p className="text-ink-muted text-sm">
          No criteria. Add one below, or leave the session judged by completion
          alone.
        </p>
      ) : null}

      {criteria.map((criterion, index) => (
        <div
          // Criteria are ordered values, not entities: two identical ones are
          // the same criterion twice and the list is replaced wholesale.
          // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
          key={index}
          data-testid="criterion"
          className="flex flex-col gap-2 rounded-button border border-hairline bg-inset px-3 py-2.5"
        >
          <div className="flex items-start gap-2">
            <span className="flex-1 text-ink-secondary text-sm">
              {describeCriterion(criterion)}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="text-ink-faint"
              aria-label="Remove criterion"
              onClick={() => onChange(criteria.filter((_, i) => i !== index))}
            >
              Remove
            </Button>
          </div>
          <CriterionFields
            criterion={criterion}
            onChange={(next) => replace(index, next)}
          />
        </div>
      ))}

      <FieldRow>
        <Field
          label="Add a criterion"
          htmlFor={`${base}-kind`}
          className="w-[190px]"
        >
          <NativeSelect
            className="w-full"
            size="sm"
            id={`${base}-kind`}
            value={kind}
            onChange={(event) => setKind(event.target.value as CriterionKind)}
          >
            {kinds.map((available) => (
              <NativeSelectOption key={available} value={available}>
                {CRITERION_KIND_LABELS[available]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onChange([...criteria, blankCriterion(kind)])}
        >
          Add
        </Button>
      </FieldRow>
    </div>
  );
}

/** The numbers of one criterion, laid out per kind. */
function CriterionFields({
  criterion,
  onChange,
}: {
  criterion: SuccessCriterion;
  onChange: (criterion: SuccessCriterion) => void;
}) {
  const base = useId();

  switch (criterion.kind) {
    case "time_in_band":
      return (
        <FieldRow>
          <PercentField
            id={`${base}-fraction`}
            label="At least"
            value={criterion.min_fraction}
            onChange={(min_fraction) =>
              onChange({ ...criterion, min_fraction })
            }
          />
          <Field
            label="Channel"
            htmlFor={`${base}-channel`}
            className="w-[110px]"
          >
            <NativeSelect
              className="w-full"
              size="sm"
              id={`${base}-channel`}
              value={criterion.band.channel}
              onChange={(event) =>
                onChange({
                  ...criterion,
                  band: {
                    ...criterion.band,
                    channel: event.target.value as Channel,
                  },
                })
              }
            >
              {(Object.keys(CHANNEL_LABELS) as Channel[]).map((channel) => (
                <NativeSelectOption key={channel} value={channel}>
                  {CHANNEL_LABELS[channel]}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
          <PercentField
            id={`${base}-band-low`}
            label="Band low"
            value={criterion.band.low}
            onChange={(low) =>
              onChange({ ...criterion, band: { ...criterion.band, low } })
            }
          />
          <PercentField
            id={`${base}-band-high`}
            label="Band high"
            value={criterion.band.high}
            onChange={(high) =>
              onChange({ ...criterion, band: { ...criterion.band, high } })
            }
          />
          <Field
            label="Applies to"
            htmlFor={`${base}-selector`}
            className="w-[124px]"
          >
            <NativeSelect
              className="w-full"
              size="sm"
              id={`${base}-selector`}
              value={
                criterion.selector.kind === "role"
                  ? (criterion.selector.role ?? "work")
                  : "all"
              }
              onChange={(event) =>
                onChange({
                  ...criterion,
                  selector:
                    event.target.value === "all"
                      ? { kind: "all", role: null, index: null }
                      : {
                          kind: "role",
                          role: event.target.value as StepRole,
                          index: null,
                        },
                })
              }
            >
              <NativeSelectOption value="all">
                The whole ride
              </NativeSelectOption>
              {(Object.keys(ROLE_LABELS) as StepRole[]).map((role) => (
                <NativeSelectOption key={role} value={role}>
                  {`${ROLE_LABELS[role]} steps`}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
        </FieldRow>
      );

    case "duration_floor":
      return (
        <FieldRow>
          <DurationField
            id={`${base}-min`}
            label="At least"
            seconds={criterion.min_seconds}
            onChange={(min_seconds) => onChange({ ...criterion, min_seconds })}
          />
        </FieldRow>
      );

    case "ceiling":
      return (
        <FieldRow>
          <Field
            label="Channel"
            htmlFor={`${base}-channel`}
            className="w-[110px]"
          >
            <NativeSelect
              className="w-full"
              size="sm"
              id={`${base}-channel`}
              value={criterion.channel}
              onChange={(event) =>
                onChange({
                  ...criterion,
                  channel: event.target.value as Channel,
                })
              }
            >
              {(Object.keys(CHANNEL_LABELS) as Channel[]).map((channel) => (
                <NativeSelectOption key={channel} value={channel}>
                  {CHANNEL_LABELS[channel]}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
          {criterion.limit.kind === "percent_of_anchor" ? (
            <PercentField
              id={`${base}-limit`}
              label="Above"
              value={criterion.limit.pct}
              onChange={(pct) =>
                onChange({
                  ...criterion,
                  limit: { ...criterion.limit, kind: "percent_of_anchor", pct },
                } as SuccessCriterion)
              }
            />
          ) : (
            <Field label="Above" htmlFor={`${base}-limit`} className="w-[92px]">
              <Input
                id={`${base}-limit`}
                inputMode="decimal"
                className="h-7 font-mono"
                defaultValue={String(criterion.limit.value)}
                onChange={(event) => {
                  const value = parseNumberInput(event.target.value);
                  if (value !== null && criterion.limit.kind === "absolute") {
                    onChange({
                      ...criterion,
                      limit: { ...criterion.limit, value },
                    });
                  }
                }}
              />
            </Field>
          )}
          <DurationField
            id={`${base}-max`}
            label="For no more than"
            seconds={criterion.max_seconds_above}
            onChange={(max_seconds_above) =>
              onChange({ ...criterion, max_seconds_above })
            }
          />
        </FieldRow>
      );

    case "sets_completed":
      return (
        <FieldRow>
          <PercentField
            id={`${base}-fraction`}
            label="At least"
            value={criterion.min_fraction}
            onChange={(min_fraction) =>
              onChange({ ...criterion, min_fraction })
            }
          />
        </FieldRow>
      );

    case "load_within":
      return (
        <FieldRow>
          <PercentField
            id={`${base}-tolerance`}
            label="Within"
            value={criterion.pct_tolerance}
            onChange={(pct_tolerance) =>
              onChange({ ...criterion, pct_tolerance })
            }
          />
        </FieldRow>
      );
  }
}

/**
 * A fraction, edited as a whole percentage.
 *
 * Uncontrolled on purpose: the value round-trips through `× 100` and `÷ 100`,
 * and re-deriving the field's text from the stored fraction on every keystroke
 * would rewrite `8` to `0.08` under the cursor. The sentence above the fields
 * is the live view of the value; the field is just where it is typed.
 */
function PercentField({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <Field label={label} hint="%" htmlFor={id} className="w-[92px]">
      <Input
        id={id}
        inputMode="decimal"
        className="h-7 font-mono"
        defaultValue={String(Math.round(value * 1000) / 10)}
        onChange={(event) => {
          const next = parseNumberInput(event.target.value);
          if (next !== null) {
            onChange(next / 100);
          }
        }}
      />
    </Field>
  );
}

/** A duration, edited as `mm:ss` and stored as seconds. */
function DurationField({
  id,
  label,
  seconds,
  onChange,
}: {
  id: string;
  label: string;
  seconds: number;
  onChange: (seconds: number) => void;
}) {
  return (
    <Field label={label} hint="mm:ss" htmlFor={id} className="w-[110px]">
      <Input
        id={id}
        className="h-7 font-mono"
        defaultValue={formatDurationClock(seconds)}
        onChange={(event) => {
          const next = parseDurationInput(event.target.value);
          if (next !== null) {
            onChange(next);
          }
        }}
      />
    </Field>
  );
}
