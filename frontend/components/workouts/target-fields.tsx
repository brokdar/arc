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
import {
  blankTarget,
  CHANNEL_ANCHORS,
  CHANNEL_UNITS,
  CHANNELS,
  type Channel,
  type DraftTarget,
} from "@/lib/workout-draft";

const CHANNEL_LABELS: Readonly<Record<Channel, string>> = {
  power: "Power",
  hr: "Heart rate",
  cadence: "Cadence",
};

const ANCHOR_LABELS: Readonly<Record<string, string>> = {
  ftp: "FTP",
  lthr: "LTHR",
  max_hr: "max HR",
};

export interface TargetFieldsProps {
  readonly label: string;
  readonly targets: readonly DraftTarget[];
  readonly onChange: (targets: readonly DraftTarget[]) => void;
}

/**
 * The per-channel targets of one step, each with its %-of-anchor / absolute
 * toggle.
 *
 * The toggle is not cosmetic: the two modes are different documents on the
 * wire (`percent_of_anchor` with an anchor and two fractions,
 * `absolute` with a unit and two numbers), and only one of them survives a
 * change of FTP. Cadence is offered in absolute form only, because no anchor
 * derives it — "80% of FTP rpm" is not a quantity.
 */
export function TargetFields({ label, targets, onChange }: TargetFieldsProps) {
  const used = new Set(targets.map((target) => target.channel));
  const available = CHANNELS.filter((channel) => !used.has(channel));

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <SectionLabel>{label}</SectionLabel>
        {available.length > 0 ? (
          <div className="flex gap-1">
            {available.map((channel) => (
              <Button
                key={channel}
                type="button"
                variant="ghost"
                size="xs"
                className="text-ink-muted"
                onClick={() => onChange([...targets, blankTarget(channel)])}
              >
                + {CHANNEL_LABELS[channel]}
              </Button>
            ))}
          </div>
        ) : null}
      </div>

      {targets.map((target) => (
        <TargetRow
          key={target.id}
          target={target}
          onChange={(next) =>
            onChange(targets.map((t) => (t.id === target.id ? next : t)))
          }
          onRemove={() => onChange(targets.filter((t) => t.id !== target.id))}
        />
      ))}
    </div>
  );
}

function TargetRow({
  target,
  onChange,
  onRemove,
}: {
  target: DraftTarget;
  onChange: (target: DraftTarget) => void;
  onRemove: () => void;
}) {
  const base = useId();
  const anchors = CHANNEL_ANCHORS[target.channel];
  const percent = target.mode === "percent";
  const unit = percent ? "%" : CHANNEL_UNITS[target.channel];
  const channelName = CHANNEL_LABELS[target.channel];

  return (
    <FieldRow className="rounded-button border border-hairline-faint bg-inset px-2.5 py-2">
      <span className="w-[68px] shrink-0 pb-1.5 text-ink-secondary text-sm">
        {channelName}
      </span>

      {anchors.length > 0 ? (
        <Field label="Kind" htmlFor={`${base}-mode`} className="w-[112px]">
          <NativeSelect
            className="w-full"
            size="sm"
            id={`${base}-mode`}
            aria-label={`${channelName} target kind`}
            value={target.mode}
            onChange={(event) =>
              onChange({
                ...target,
                mode: event.target.value === "percent" ? "percent" : "absolute",
              })
            }
          >
            <NativeSelectOption value="percent">% of anchor</NativeSelectOption>
            <NativeSelectOption value="absolute">Absolute</NativeSelectOption>
          </NativeSelect>
        </Field>
      ) : null}

      {percent && anchors.length > 1 ? (
        <Field label="Anchor" htmlFor={`${base}-anchor`} className="w-[92px]">
          <NativeSelect
            className="w-full"
            size="sm"
            id={`${base}-anchor`}
            aria-label={`${channelName} anchor`}
            value={target.anchorType}
            onChange={(event) =>
              onChange({
                ...target,
                anchorType: event.target.value as DraftTarget["anchorType"],
              })
            }
          >
            {anchors.map((anchor) => (
              <NativeSelectOption key={anchor} value={anchor}>
                {ANCHOR_LABELS[anchor] ?? anchor}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
      ) : null}

      <Field
        label="Low"
        hint={percent ? `% ${ANCHOR_LABELS[target.anchorType] ?? ""}` : unit}
        htmlFor={`${base}-low`}
        className="w-[84px]"
      >
        <Input
          id={`${base}-low`}
          aria-label={`${channelName} low`}
          inputMode="decimal"
          className="h-7 font-mono"
          value={target.low}
          onChange={(event) => onChange({ ...target, low: event.target.value })}
        />
      </Field>

      <Field
        label="High"
        hint={percent ? `% ${ANCHOR_LABELS[target.anchorType] ?? ""}` : unit}
        htmlFor={`${base}-high`}
        className="w-[84px]"
      >
        <Input
          id={`${base}-high`}
          aria-label={`${channelName} high`}
          inputMode="decimal"
          className="h-7 font-mono"
          value={target.high}
          onChange={(event) =>
            onChange({ ...target, high: event.target.value })
          }
        />
      </Field>

      <Button
        type="button"
        variant="ghost"
        size="xs"
        className="mb-0.5 ml-auto text-ink-faint"
        aria-label={`Remove ${channelName} target`}
        onClick={onRemove}
      >
        Remove
      </Button>
    </FieldRow>
  );
}
