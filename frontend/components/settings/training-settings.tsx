"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import { Field, FieldRow } from "@/components/design/field";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";
import {
  ASK_FREQUENCY_LABELS,
  type AskFrequency,
  LIFTED_BEFORE_LABELS,
  LOCALE_LABELS,
  type Locale,
  MODE_HINTS,
  MODE_LABELS,
  type TrainingMode,
  type TrainingSettings,
  trainingSettingsQueryKey,
  UNITS_LABELS,
  type Units,
} from "@/lib/training-settings";

type Update = components["schemas"]["TrainingSettingsUpdate"];

/**
 * Training: how much arc asks and shows in the gym, stored once and read by
 * every later surface.
 *
 * Each control saves on change and sends only itself: these are independent
 * switches, not one record to submit, and a form-wide PATCH would rewrite a
 * mode the first-run answer set a moment ago with whatever the form loaded.
 * What is shown is always what the server stored — a refused change leaves
 * the old value and says why.
 */
export function TrainingSettingsPanel({
  className,
}: {
  readonly className?: string;
}) {
  const headingId = useId();
  const settings = $api.useQuery("get", "/api/v1/settings/training");

  return (
    <Panel className={className} role="region" aria-labelledby={headingId}>
      <div className="flex flex-col gap-3.5 px-5 py-4">
        <SectionLabel level={2} id={headingId}>
          Training
        </SectionLabel>
        {settings.isPending ? (
          <p className="text-ink-muted text-sm">Loading training settings…</p>
        ) : !settings.data ? (
          <p role="alert" className="text-destructive text-sm">
            {loadFailureMessage(settings.error, "the training settings")}
          </p>
        ) : (
          <TrainingFields settings={settings.data} />
        )}
      </div>
    </Panel>
  );
}

function TrainingFields({ settings }: { readonly settings: TrainingSettings }) {
  const base = useId();
  const queryClient = useQueryClient();
  const save = $api.useMutation("patch", "/api/v1/settings/training", {
    onSuccess: (updated) => {
      queryClient.setQueryData(trainingSettingsQueryKey, updated);
    },
  });
  const problems = apiErrorMessages(save.error);
  const send = (body: Update) => save.mutate({ body });

  return (
    <div className="flex flex-col gap-3.5">
      <FieldRow>
        <Field
          label="Mode"
          htmlFor={`${base}-mode`}
          className="flex-[1_1_220px]"
        >
          <NativeSelect
            id={`${base}-mode`}
            className="w-full"
            value={settings.mode}
            onChange={(event) =>
              send({ mode: event.target.value as TrainingMode })
            }
          >
            {(Object.keys(MODE_LABELS) as TrainingMode[]).map((mode) => (
              <NativeSelectOption key={mode} value={mode}>
                {MODE_LABELS[mode]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field
          label="Ask how hard a set felt"
          htmlFor={`${base}-ask`}
          className="flex-[1_1_220px]"
        >
          <NativeSelect
            id={`${base}-ask`}
            className="w-full"
            value={settings.ask_frequency}
            onChange={(event) =>
              send({ ask_frequency: event.target.value as AskFrequency })
            }
          >
            {(Object.keys(ASK_FREQUENCY_LABELS) as AskFrequency[]).map(
              (option) => (
                <NativeSelectOption key={option} value={option}>
                  {ASK_FREQUENCY_LABELS[option]}
                </NativeSelectOption>
              ),
            )}
          </NativeSelect>
        </Field>
      </FieldRow>
      <p className="text-ink-muted text-sm">{MODE_HINTS[settings.mode]}</p>

      <FieldRow>
        <Field
          label="Units"
          htmlFor={`${base}-units`}
          className="flex-[1_1_160px]"
        >
          <NativeSelect
            id={`${base}-units`}
            className="w-full"
            value={settings.units}
            onChange={(event) => send({ units: event.target.value as Units })}
          >
            {(Object.keys(UNITS_LABELS) as Units[]).map((units) => (
              <NativeSelectOption key={units} value={units}>
                {UNITS_LABELS[units]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field
          label="Language"
          htmlFor={`${base}-locale`}
          className="flex-[1_1_160px]"
        >
          <NativeSelect
            id={`${base}-locale`}
            className="w-full"
            value={settings.locale}
            onChange={(event) => send({ locale: event.target.value as Locale })}
          >
            {(Object.keys(LOCALE_LABELS) as Locale[]).map((locale) => (
              <NativeSelectOption key={locale} value={locale}>
                {LOCALE_LABELS[locale]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
      </FieldRow>

      <label className="flex items-start gap-2.5 text-ink-secondary text-sm">
        <input
          type="checkbox"
          className="mt-1 size-3.5 shrink-0 accent-accent"
          checked={settings.plain_logbook}
          onChange={(event) => send({ plain_logbook: event.target.checked })}
        />
        <span>
          Plain logbook
          <span className="block text-ink-muted">
            Log what you lifted and nothing more — no suggested weights. For
            following a plan someone else wrote, like a physio&apos;s.
          </span>
        </span>
      </label>
      <label className="flex items-start gap-2.5 text-ink-secondary text-sm">
        <input
          type="checkbox"
          className="mt-1 size-3.5 shrink-0 accent-accent"
          checked={settings.high_contrast}
          onChange={(event) => send({ high_contrast: event.target.checked })}
        />
        <span>
          High contrast
          <span className="block text-ink-muted">For bright or dim gyms.</span>
        </span>
      </label>

      <p className="text-ink-faint text-xs">
        Lifted before:{" "}
        {settings.lifted_before === null
          ? "not answered"
          : LIFTED_BEFORE_LABELS[settings.lifted_before]}
      </p>
      {problems.length > 0 ? (
        <p role="alert" className="text-destructive text-sm">
          {problems.join(" ")}
        </p>
      ) : null}
    </div>
  );
}
