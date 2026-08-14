"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Field, FieldRow } from "@/components/design/field";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { todayIsoDate, weekdayLabel } from "@/lib/dates";
import { formatDayMonthYear } from "@/lib/format";
import {
  anchorLabel,
  confounderLabel,
  fromInputValue,
  MARKER_FIELDS,
  type NumericField,
  orderByTier,
  RATING_FIELDS,
  ratingLabel,
  SLEEP_FIELDS,
  scalePoints,
  toInputValue,
  type WellnessDay,
  type WellnessInputs,
  type WellnessWrite,
} from "@/lib/wellness";

/**
 * The one consolidated daily touchpoint: one form, one day, one save.
 *
 * Everything the form *says* — the scales, their anchor descriptors, their
 * polarity, the confounder vocabulary and which of those void a morning —
 * comes from `/wellness/inputs`, never from a copy in here. That endpoint
 * exists so the UI and the coaching agent cannot drift apart on what a 3
 * means, and a hard-coded label in this file would be the drift.
 *
 * The form posts a **PATCH of everything it shows**, which is what makes an
 * emptied box a retraction rather than a no-op: on this resource `null` clears
 * and omission leaves alone, and a form that sent only what changed would make
 * "I deleted that wrong HRV" indistinguishable from "I did not touch it".
 */
export function WellnessForm({
  date,
  day,
  inputs,
  lastHrv,
  className,
}: {
  readonly date: string;
  /** The stored day, or null when nothing has been recorded for this date. */
  readonly day: WellnessDay | null;
  readonly inputs: WellnessInputs | undefined;
  /**
   * How the athlete's most recent HRV reading was taken, if there is one.
   *
   * Seeds the two descriptors for a *new* reading, because an athlete reads
   * the same number off the same app every morning. It is a seed and not a
   * default: on the very first HRV reading there is nothing to inherit, and
   * the form asks rather than guessing.
   */
  readonly lastHrv: HrvDescriptors | null;
  readonly className?: string;
}) {
  // Keyed by date in the parent, so switching days remounts and reseeds rather
  // than reconciling — a form that kept yesterday's numbers while showing
  // today's date is the one bug this page cannot afford.
  return (
    <Panel className={className}>
      <div className="flex flex-col gap-4 px-5 py-4">
        <div className="flex items-baseline justify-between gap-3">
          <SectionLabel level={2}>
            {date === todayIsoDate() ? "This morning" : "That morning"}
          </SectionLabel>
          <span className="font-mono text-ink-faint text-xs">
            {weekdayLabel(date)} {formatDayMonthYear(date)}
          </span>
        </div>
        <Fields
          key={date}
          date={date}
          day={day}
          inputs={inputs}
          lastHrv={lastHrv}
        />
      </div>
    </Panel>
  );
}

function Fields({
  date,
  day,
  inputs,
  lastHrv,
}: {
  readonly date: string;
  readonly day: WellnessDay | null;
  readonly inputs: WellnessInputs | undefined;
  readonly lastHrv: HrvDescriptors | null;
}) {
  const base = useId();
  const queryClient = useQueryClient();
  const [numbers, setNumbers] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      [...SLEEP_FIELDS, ...MARKER_FIELDS].map((spec) => [
        spec.field,
        toInputValue(day, spec),
      ]),
    ),
  );
  const [ratings, setRatings] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      RATING_FIELDS.map((field) => [field, String(day?.[field] ?? "")]),
    ),
  );
  // Never a hard-coded `rmssd`. The whole reason `hrv_ms` carries a statistic
  // is that this athlete's watch reports SDNN, so a form that stamped RMSSD on
  // every hand-typed reading would poison the series the discriminator exists
  // to keep clean.
  const [hrv, setHrv] = useState<HrvDescriptors>({
    hrv_metric: day?.hrv_metric ?? lastHrv?.hrv_metric ?? null,
    hrv_context: day?.hrv_context ?? lastHrv?.hrv_context ?? null,
  });
  const [times, setTimes] = useState({
    sleep_start_local: day?.sleep_start_local?.slice(0, 5) ?? "",
    sleep_end_local: day?.sleep_end_local?.slice(0, 5) ?? "",
  });
  const [tags, setTags] = useState<readonly string[]>(day?.confounders ?? []);
  const [note, setNote] = useState(day?.note ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = $api.useMutation("patch", "/api/v1/wellness/days/{local_date}", {
    onSuccess: () => {
      setSaved(true);
      // Every wellness read is invalidated rather than the one day patched in:
      // the history table, the weight in force and Today's card all derive
      // from this write, and a hand-patched cache would have to know which.
      queryClient.invalidateQueries({
        predicate: (query) =>
          JSON.stringify(query.queryKey).includes("/api/v1/wellness"),
      });
    },
  });

  const scales = new Map(
    (inputs?.scales ?? []).map((scale) => [scale.field, scale]),
  );
  const sleep = orderByTier(SLEEP_FIELDS, inputs?.tiers);
  const markers = orderByTier(MARKER_FIELDS, inputs?.tiers);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaved(false);
    setProblem(null);
    save.reset();
    const body: WellnessWrite = {};
    for (const spec of [...SLEEP_FIELDS, ...MARKER_FIELDS]) {
      const parsed = fromInputValue(numbers[spec.field] ?? "", spec);
      if (parsed !== undefined) {
        Object.assign(body, { [spec.field]: parsed });
      }
    }
    // The clock times carry no date — the date is the day's. An overnight
    // reading belongs to the **wake** day, so a night that starts at 23:30 is
    // filed under the morning the athlete reads it on.
    for (const field of ["sleep_start_local", "sleep_end_local"] as const) {
      const raw = times[field].trim();
      Object.assign(body, { [field]: raw === "" ? null : `${raw}:00` });
    }
    // HRV cannot be stored without saying which statistic it is and how it was
    // taken: the two are not decoration, they are what makes the number join a
    // baseline honestly. Refused here rather than at the API so the athlete
    // reads it beside the field instead of after a round trip.
    if (typeof body.hrv_ms === "number") {
      if (!hrv.hrv_metric || !hrv.hrv_context) {
        setProblem(
          "An HRV reading needs to say which statistic it is and how it was " +
            "taken — the two are not the same series, so a baseline cannot " +
            "pool them.",
        );
        return;
      }
      body.hrv_metric = hrv.hrv_metric;
      body.hrv_context = hrv.hrv_context;
    }
    for (const field of RATING_FIELDS) {
      const raw = ratings[field] ?? "";
      Object.assign(body, { [field]: raw === "" ? null : Number(raw) });
    }
    body.confounders = [...tags] as WellnessWrite["confounders"];
    body.note = note.trim() === "" ? null : note.trim();
    save.mutate({ params: { path: { local_date: date } }, body });
  }

  const problems = [
    ...(problem ? [problem] : []),
    ...apiErrorMessages(save.error),
  ];

  return (
    // Named, so it is a landmark: the page now carries a trajectory chart per
    // metric beside this form, and "Resting HR" is the accessible name of two
    // quite different things without it.
    <form
      aria-label="Record this day"
      className="flex flex-col gap-4"
      onSubmit={submit}
    >
      <Group label="Overnight">
        {sleep.map((spec) => (
          <NumberField
            key={spec.field}
            base={base}
            spec={spec}
            value={numbers[spec.field] ?? ""}
            onChange={(next) => {
              setNumbers((current) => ({ ...current, [spec.field]: next }));
              setSaved(false);
            }}
          />
        ))}
        {(
          [
            ["sleep_start_local", "Asleep at"],
            ["sleep_end_local", "Awake at"],
          ] as const
        ).map(([field, label]) => (
          <Field
            key={field}
            label={label}
            hint="local"
            htmlFor={`${base}-${field}`}
            className="flex-[1_1_120px]"
          >
            <Input
              id={`${base}-${field}`}
              type="time"
              className="font-mono"
              value={times[field]}
              onChange={(event) => {
                setTimes((current) => ({
                  ...current,
                  [field]: event.target.value,
                }));
                setSaved(false);
              }}
            />
          </Field>
        ))}
      </Group>

      <Group label="Markers">
        {markers.map((spec) => (
          <NumberField
            key={spec.field}
            base={base}
            spec={spec}
            value={numbers[spec.field] ?? ""}
            onChange={(next) => {
              setNumbers((current) => ({ ...current, [spec.field]: next }));
              setSaved(false);
            }}
          />
        ))}
      </Group>

      {/* Shown only once there is a reading to describe, because that is when
          the two become required — and hidden otherwise so a form answered
          without HRV is not two selects longer than it needs to be. */}
      {numbers.hrv_ms?.trim() ? (
        <HrvDescriptorFields
          base={base}
          value={hrv}
          onChange={(next) => {
            setHrv(next);
            setSaved(false);
          }}
        />
      ) : null}

      <Group label="How you feel">
        {RATING_FIELDS.map((field) => {
          const scale = scales.get(field);
          return (
            <Field
              key={field}
              label={ratingLabel(field)}
              hint={scale ? `${scale.low}–${scale.high}` : undefined}
              htmlFor={`${base}-${field}`}
              className="flex-[1_1_200px]"
            >
              <NativeSelect
                id={`${base}-${field}`}
                className="w-full"
                value={ratings[field] ?? ""}
                onChange={(event) => {
                  setRatings((current) => ({
                    ...current,
                    [field]: event.target.value,
                  }));
                  setSaved(false);
                }}
              >
                <NativeSelectOption value="">Not recorded</NativeSelectOption>
                {scale
                  ? scalePoints(scale).map((point) => (
                      <NativeSelectOption key={point} value={String(point)}>
                        {anchorLabel(scale, point)}
                      </NativeSelectOption>
                    ))
                  : null}
              </NativeSelect>
            </Field>
          );
        })}
      </Group>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1.5">
          <SectionLabel>What was true about the night</SectionLabel>
        </legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {(inputs?.confounders ?? []).map((entry) => (
            <label
              key={entry.value}
              className="flex items-center gap-1.5 text-ink-secondary text-sm"
              // Which tags void a morning is served, never assumed here: it is
              // the athlete's own pre-check and it can change without this
              // build changing.
              title={
                entry.invalidates_markers
                  ? "Marks this morning's device numbers as recorded but not actionable"
                  : undefined
              }
            >
              <input
                type="checkbox"
                className="accent-accent"
                checked={tags.includes(entry.value)}
                onChange={(event) => {
                  setTags((current) =>
                    event.target.checked
                      ? [...current, entry.value]
                      : current.filter((tag) => tag !== entry.value),
                  );
                  setSaved(false);
                }}
              />
              {confounderLabel(entry.value)}
              {entry.invalidates_markers ? (
                <span aria-hidden className="text-ink-faint text-2xs">
                  ⚠
                </span>
              ) : null}
            </label>
          ))}
        </div>
      </fieldset>

      <Field label="Note" htmlFor={`${base}-note`}>
        <Input
          id={`${base}-note`}
          value={note}
          placeholder="Anything the tags above have no word for"
          onChange={(event) => {
            setNote(event.target.value);
            setSaved(false);
          }}
        />
      </Field>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save the day"}
        </Button>
        {saved && problems.length === 0 ? (
          <p role="status" className="text-ink-muted text-sm">
            Saved.
          </p>
        ) : null}
      </div>

      {problems.length > 0 ? (
        <ul
          role="alert"
          className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
        >
          {problems.map((entry) => (
            <li key={entry}>{entry}</li>
          ))}
        </ul>
      ) : null}
    </form>
  );
}

function Group({
  label,
  children,
}: {
  readonly label: string;
  readonly children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <SectionLabel>{label}</SectionLabel>
      <FieldRow>{children}</FieldRow>
    </div>
  );
}

function NumberField({
  base,
  spec,
  value,
  onChange,
}: {
  readonly base: string;
  readonly spec: NumericField;
  readonly value: string;
  readonly onChange: (next: string) => void;
}) {
  return (
    <Field
      label={spec.label}
      hint={spec.hint}
      htmlFor={`${base}-${spec.field}`}
      className="flex-[1_1_140px]"
    >
      <Input
        id={`${base}-${spec.field}`}
        inputMode="decimal"
        className="font-mono"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}

/** What an HRV number is: which statistic, and how it was taken. */
export interface HrvDescriptors {
  readonly hrv_metric: WellnessWrite["hrv_metric"] | null;
  readonly hrv_context: WellnessWrite["hrv_context"] | null;
}

//: The statistic Apple Health publishes is SDNN, and it is listed first
//: because it is the one this athlete can actually read off their phone.
const HRV_METRICS = [
  ["sdnn", "SDNN — what Apple Health shows"],
  ["rmssd", "RMSSD — what a dedicated HRV app shows"],
] as const;

const HRV_CONTEXTS = [
  ["sleeping", "Overnight average"],
  ["waking_spot", "Spot reading after waking"],
  ["manual", "Measured on purpose"],
] as const;

/**
 * The two things an HRV reading has to say about itself.
 *
 * Asked rather than assumed, and that is the whole point of the field pair.
 * SDNN and RMSSD are not on one scale, this athlete's watch reports SDNN, and
 * a form that defaulted either way would put numbers into a series they do not
 * belong to — invisibly, because both are plausible millisecond figures.
 *
 * The selects are seeded from the athlete's most recent reading, which makes
 * answering them a once-per-source decision rather than a daily chore; on the
 * very first reading there is nothing to inherit and the form asks.
 */
function HrvDescriptorFields({
  base,
  value,
  onChange,
}: {
  readonly base: string;
  readonly value: HrvDescriptors;
  readonly onChange: (next: HrvDescriptors) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <FieldRow>
        <Field
          label="HRV statistic"
          htmlFor={`${base}-hrv-metric`}
          className="flex-[1_1_240px]"
        >
          <NativeSelect
            id={`${base}-hrv-metric`}
            className="w-full"
            value={value.hrv_metric ?? ""}
            onChange={(event) =>
              onChange({
                ...value,
                hrv_metric: (event.target.value ||
                  null) as HrvDescriptors["hrv_metric"],
              })
            }
          >
            <NativeSelectOption value="">Which one?</NativeSelectOption>
            {HRV_METRICS.map(([option, label]) => (
              <NativeSelectOption key={option} value={option}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field
          label="How it was taken"
          htmlFor={`${base}-hrv-context`}
          className="flex-[1_1_240px]"
        >
          <NativeSelect
            id={`${base}-hrv-context`}
            className="w-full"
            value={value.hrv_context ?? ""}
            onChange={(event) =>
              onChange({
                ...value,
                hrv_context: (event.target.value ||
                  null) as HrvDescriptors["hrv_context"],
              })
            }
          >
            <NativeSelectOption value="">How?</NativeSelectOption>
            {HRV_CONTEXTS.map(([option, label]) => (
              <NativeSelectOption key={option} value={option}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
      </FieldRow>
      <p className="text-ink-faint text-xs">
        A baseline is built within one statistic and one context — an overnight
        SDNN and a daytime RMSSD are not the same series, so these are asked
        rather than assumed. Answered once, they carry over to the next reading.
      </p>
    </div>
  );
}
