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
import { apiErrorMessages } from "@/lib/api-errors";
import { useT } from "@/lib/i18n/provider";
import { refreshPacketAfterSettingsChange } from "@/lib/offline/packet-store";
import {
  ASK_CHOICES,
  type AskChoice,
  askChoiceOf,
  askFrequencyOf,
  LIFTED_BEFORE_KEYS,
  LOCALE_LABELS,
  type Locale,
  MODE_HINT_KEYS,
  MODE_KEYS,
  type TrainingMode,
  type TrainingSettings,
  trainingSettingsQueryKey,
  UNITS_KEYS,
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
  const t = useT();
  const settings = $api.useQuery("get", "/api/v1/settings/training");

  return (
    <Panel className={className} role="region" aria-labelledby={headingId}>
      <div className="flex flex-col gap-3.5 px-5 py-4">
        <SectionLabel level={2} id={headingId}>
          {t("settings.training.title")}
        </SectionLabel>
        {settings.isPending ? (
          <p className="text-ink-muted text-sm">
            {t("settings.training.loading")}
          </p>
        ) : !settings.data ? (
          <p role="alert" className="text-destructive text-sm">
            {t("settings.training.loadFailed")}
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
  const t = useT();
  const queryClient = useQueryClient();
  const save = $api.useMutation("patch", "/api/v1/settings/training", {
    onSuccess: (updated) => {
      queryClient.setQueryData(trainingSettingsQueryKey, updated);
      void refreshPacketAfterSettingsChange();
    },
  });
  const problems = apiErrorMessages(save.error);
  const send = (body: Update) => save.mutate({ body });

  return (
    <div className="flex flex-col gap-3.5">
      <FieldRow>
        <Field
          label={t("settings.training.mode")}
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
            {(Object.keys(MODE_KEYS) as TrainingMode[]).map((mode) => (
              <NativeSelectOption key={mode} value={mode}>
                {t(MODE_KEYS[mode])}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field
          label={t("settings.training.ask")}
          htmlFor={`${base}-ask`}
          className="flex-[1_1_220px]"
        >
          <NativeSelect
            id={`${base}-ask`}
            className="w-full"
            value={askChoiceOf(settings.ask_frequency)}
            onChange={(event) =>
              send({
                ask_frequency: askFrequencyOf(event.target.value as AskChoice),
              })
            }
          >
            {(Object.keys(ASK_CHOICES) as AskChoice[]).map((choice) => (
              <NativeSelectOption key={choice} value={choice}>
                {t(ASK_CHOICES[choice])}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
      </FieldRow>
      <p className="text-ink-muted text-sm">
        {t(MODE_HINT_KEYS[settings.mode])}
      </p>
      <p className="text-ink-muted text-sm">{t("settings.training.askHelp")}</p>

      <FieldRow>
        <Field
          label={t("settings.training.units")}
          htmlFor={`${base}-units`}
          className="flex-[1_1_160px]"
        >
          <NativeSelect
            id={`${base}-units`}
            className="w-full"
            value={settings.units}
            onChange={(event) => send({ units: event.target.value as Units })}
          >
            {(Object.keys(UNITS_KEYS) as Units[]).map((units) => (
              <NativeSelectOption key={units} value={units}>
                {t(UNITS_KEYS[units])}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field
          label={t("settings.training.language")}
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

      <Toggle
        checked={settings.plain_logbook}
        onChange={(checked) => send({ plain_logbook: checked })}
        label={t("settings.training.plain")}
        help={t("settings.training.plainHelp")}
      />
      <Toggle
        checked={settings.high_contrast}
        onChange={(checked) => send({ high_contrast: checked })}
        label={t("settings.training.contrast")}
        help={t("settings.training.contrastHelp")}
      />
      {/* The only way back from the first-run box: once ticked, max efforts
          stayed off for good because nothing in Settings could undo it. */}
      <Toggle
        checked={settings.no_max_efforts}
        onChange={(checked) => send({ no_max_efforts: checked })}
        label={t("settings.training.under18")}
        help={t("settings.training.under18Help")}
      />

      <p className="text-ink-faint text-xs">
        {t("settings.training.liftedBefore", {
          answer:
            settings.lifted_before === null
              ? t("settings.training.notAnswered")
              : t(LIFTED_BEFORE_KEYS[settings.lifted_before]),
        })}
      </p>
      {problems.length > 0 ? (
        <p role="alert" className="text-destructive text-sm">
          {problems.join(" ")}
        </p>
      ) : null}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  help,
}: {
  readonly checked: boolean;
  readonly onChange: (checked: boolean) => void;
  readonly label: string;
  readonly help: string;
}) {
  return (
    <label className="flex items-start gap-2.5 text-ink-secondary text-sm">
      <input
        type="checkbox"
        className="mt-1 size-3.5 shrink-0 accent-accent"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        {label}
        <span className="block text-ink-muted">{help}</span>
      </span>
    </label>
  );
}
