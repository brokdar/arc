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
import { TargetFields } from "@/components/workouts/target-fields";
import { cn } from "@/lib/utils";
import {
  blankRampStep,
  blankRepeatBlock,
  blankSteadyStep,
  type DraftRepeatBlock,
  type DraftStep,
  insertStep,
  MAX_NESTING_DEPTH,
  moveStep,
  removeStep,
  repeatDepth,
  replaceStep,
  STEP_ROLES,
  type StepRole,
} from "@/lib/workout-draft";

const ROLE_LABELS: Readonly<Record<StepRole, string>> = {
  warmup: "Warm-up",
  work: "Work",
  recovery: "Recovery",
  rest: "Rest",
  cooldown: "Cool-down",
};

export interface EnduranceBuilderProps {
  readonly steps: readonly DraftStep[];
  readonly onChange: (steps: readonly DraftStep[]) => void;
}

/**
 * The step-tree editor.
 *
 * The prescription is a recursive tree — steady steps, ramps and repeat blocks
 * that may contain any of the three — and the editor mirrors it literally: a
 * repeat block renders its children indented inside itself, and the "add"
 * buttons at the foot of a block add *into* that block. There is no flat list
 * with an indent column, because a flat list cannot say which iteration a step
 * belongs to.
 *
 * Every edit goes through the pure helpers in `lib/workout-draft.ts`, keyed by
 * the node's client id, so this component holds no state of its own and the
 * live profile preview above it is a function of the same tree.
 */
export function EnduranceBuilder({ steps, onChange }: EnduranceBuilderProps) {
  return (
    <div className="flex flex-col gap-2.5">
      {steps.length === 0 ? (
        <p className="rounded-button border border-hairline border-dashed px-3.5 py-4 text-ink-muted text-sm">
          No steps yet. Add a warm-up, then the work — or a repeat block if the
          session has intervals.
        </p>
      ) : null}

      {steps.map((step, index) => (
        <StepNode
          key={step.id}
          step={step}
          index={index}
          siblings={steps.length}
          steps={steps}
          onChange={onChange}
        />
      ))}

      <AddButtons parentId={null} steps={steps} onChange={onChange} depth={0} />
    </div>
  );
}

function AddButtons({
  parentId,
  steps,
  onChange,
  depth,
}: {
  parentId: string | null;
  steps: readonly DraftStep[];
  onChange: (steps: readonly DraftStep[]) => void;
  depth: number;
}) {
  const label = parentId === null ? "" : " inside";
  return (
    <div className="flex flex-wrap gap-1.5">
      <Button
        type="button"
        variant="secondary"
        size="sm"
        onClick={() => onChange(insertStep(steps, parentId, blankSteadyStep()))}
      >
        {`Add steady step${label}`}
      </Button>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        onClick={() => onChange(insertStep(steps, parentId, blankRampStep()))}
      >
        {`Add ramp${label}`}
      </Button>
      {depth < MAX_NESTING_DEPTH ? (
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() =>
            onChange(insertStep(steps, parentId, blankRepeatBlock()))
          }
        >
          {`Add repeat${label}`}
        </Button>
      ) : null}
    </div>
  );
}

interface StepNodeProps {
  readonly step: DraftStep;
  readonly index: number;
  readonly siblings: number;
  readonly steps: readonly DraftStep[];
  readonly onChange: (steps: readonly DraftStep[]) => void;
}

function StepNode({ step, index, siblings, steps, onChange }: StepNodeProps) {
  const replace = (next: DraftStep) =>
    onChange(replaceStep(steps, step.id, next));
  const remove = () => onChange(removeStep(steps, step.id));
  const move = (delta: number) => onChange(moveStep(steps, step.id, delta));

  const header = (
    <div className="flex items-center gap-1.5">
      <SectionLabel className="mr-auto">
        {step.kind === "repeat"
          ? `Repeat · ${step.children.length} step${step.children.length === 1 ? "" : "s"}`
          : step.kind === "ramp"
            ? "Ramp"
            : "Steady"}
      </SectionLabel>
      <Button
        type="button"
        variant="ghost"
        size="xs"
        aria-label="Move earlier"
        disabled={index === 0}
        className="text-ink-faint"
        onClick={() => move(-1)}
      >
        ↑
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="xs"
        aria-label="Move later"
        disabled={index === siblings - 1}
        className="text-ink-faint"
        onClick={() => move(1)}
      >
        ↓
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="xs"
        aria-label="Remove step"
        className="text-ink-faint"
        onClick={remove}
      >
        Remove
      </Button>
    </div>
  );

  if (step.kind === "repeat") {
    return (
      <RepeatNode
        block={step}
        header={header}
        steps={steps}
        onChange={onChange}
        onReplace={replace}
      />
    );
  }

  return (
    <div
      data-testid="draft-step"
      className="flex flex-col gap-2.5 rounded-card border border-hairline-card bg-card px-3.5 py-3"
    >
      {header}
      <ExtentFields step={step} onChange={replace} />
      {step.kind === "ramp" ? (
        <div className="flex flex-col gap-2">
          <TargetFields
            label="From"
            targets={step.startTargets}
            onChange={(startTargets) => replace({ ...step, startTargets })}
          />
          <TargetFields
            label="To"
            targets={step.endTargets}
            onChange={(endTargets) => replace({ ...step, endTargets })}
          />
        </div>
      ) : (
        <TargetFields
          label="Targets"
          targets={step.targets}
          onChange={(targets) => replace({ ...step, targets })}
        />
      )}
    </div>
  );
}

function RepeatNode({
  block,
  header,
  steps,
  onChange,
  onReplace,
}: {
  block: DraftRepeatBlock;
  header: React.ReactNode;
  steps: readonly DraftStep[];
  onChange: (steps: readonly DraftStep[]) => void;
  onReplace: (next: DraftStep) => void;
}) {
  const base = useId();
  const depth = repeatDepth(steps, block.id);

  return (
    <div
      data-testid="draft-step"
      className="flex flex-col gap-2.5 rounded-card border border-accent-border border-dashed bg-accent-surface px-3.5 py-3"
      data-repeat-depth={depth}
    >
      {header}
      <Field label="Times" htmlFor={`${base}-times`} className="w-[92px]">
        <Input
          id={`${base}-times`}
          inputMode="numeric"
          className="h-7 font-mono"
          value={block.times}
          onChange={(event) =>
            onReplace({ ...block, times: event.target.value })
          }
        />
      </Field>

      <div className="flex flex-col gap-2.5 border-hairline border-l pl-3">
        {block.children.map((child, childIndex) => (
          <StepNode
            key={child.id}
            step={child}
            index={childIndex}
            siblings={block.children.length}
            steps={steps}
            onChange={onChange}
          />
        ))}
        <AddButtons
          parentId={block.id}
          steps={steps}
          onChange={onChange}
          depth={depth}
        />
      </div>
    </div>
  );
}

/** Duration or distance — the domain insists on exactly one, so the UI does. */
function ExtentFields({
  step,
  onChange,
}: {
  step: Exclude<DraftStep, DraftRepeatBlock>;
  onChange: (next: DraftStep) => void;
}) {
  const base = useId();
  return (
    <FieldRow>
      <Field label="Role" htmlFor={`${base}-role`} className="w-[110px]">
        <NativeSelect
          className="w-full"
          size="sm"
          id={`${base}-role`}
          value={step.role}
          onChange={(event) =>
            onChange({ ...step, role: event.target.value as StepRole })
          }
        >
          {STEP_ROLES.map((role) => (
            <NativeSelectOption key={role} value={role}>
              {ROLE_LABELS[role]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Field>

      <Field
        label="Measured by"
        htmlFor={`${base}-extent`}
        className="w-[104px]"
      >
        <NativeSelect
          className="w-full"
          size="sm"
          id={`${base}-extent`}
          value={step.extent}
          onChange={(event) =>
            onChange({
              ...step,
              extent:
                event.target.value === "distance" ? "distance" : "duration",
            })
          }
        >
          <NativeSelectOption value="duration">Duration</NativeSelectOption>
          <NativeSelectOption value="distance">Distance</NativeSelectOption>
        </NativeSelect>
      </Field>

      {step.extent === "duration" ? (
        <Field
          label="Duration"
          hint="mm:ss"
          htmlFor={`${base}-duration`}
          className="w-[92px]"
        >
          <Input
            id={`${base}-duration`}
            className={cn("h-7 font-mono")}
            placeholder="4:00"
            value={step.duration}
            onChange={(event) =>
              onChange({ ...step, duration: event.target.value })
            }
          />
        </Field>
      ) : (
        <Field
          label="Distance"
          hint="km"
          htmlFor={`${base}-distance`}
          className="w-[92px]"
        >
          <Input
            id={`${base}-distance`}
            inputMode="decimal"
            className="h-7 font-mono"
            value={step.distanceKm}
            onChange={(event) =>
              onChange({ ...step, distanceKm: event.target.value })
            }
          />
        </Field>
      )}

      <Field
        label="Name"
        hint="optional"
        htmlFor={`${base}-name`}
        className="min-w-[140px] flex-1"
      >
        <Input
          id={`${base}-name`}
          className="h-7"
          placeholder={ROLE_LABELS[step.role]}
          value={step.name}
          onChange={(event) => onChange({ ...step, name: event.target.value })}
        />
      </Field>
    </FieldRow>
  );
}
