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
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";
import { parseNumberInput } from "@/lib/format";

type Schemas = components["schemas"];
type Athlete = Schemas["AthleteRead"];
type Sex = Schemas["Sex"];

const SEX_LABELS: Readonly<Record<Sex, string>> = {
  female: "Female",
  male: "Male",
  unspecified: "Prefer not to say",
};

const athleteQueryKey = $api.queryOptions("get", "/api/v1/athlete").queryKey;

/**
 * Who the athlete is: the four facts the profile holds and nothing else.
 *
 * Not on this form, deliberately: `plan_state` is edited where the plan is
 * (the calendar's pause control, D58) and the illness flag has its own panel,
 * because both change what the *application does* and neither is a fact about
 * the person. `capabilities` is an unmodelled stub the MVP stores and never
 * interprets (`app.domain.athlete`), so there is nothing here to edit it with.
 */
export function ProfileForm({ className }: { readonly className?: string }) {
  const athlete = $api.useQuery("get", "/api/v1/athlete");

  return (
    <Panel className={className}>
      <div className="flex flex-col gap-3.5 px-5 py-4">
        <SectionLabel level={2}>Profile</SectionLabel>
        {athlete.isPending ? (
          <p className="text-ink-muted text-sm">Loading the profile…</p>
        ) : athlete.error || !athlete.data ? (
          <p role="alert" className="text-destructive text-sm">
            {loadFailureMessage(athlete.error, "the profile")}
          </p>
        ) : (
          // Split in two so the fields can seed their state from a profile
          // that has actually arrived. A single component would have to seed
          // from `undefined` and then reconcile, which is the shape of a form
          // that overwrites what you are typing when a refetch lands.
          <ProfileFields athlete={athlete.data} />
        )}
      </div>
    </Panel>
  );
}

function ProfileFields({ athlete }: { readonly athlete: Athlete }) {
  const base = useId();
  const queryClient = useQueryClient();
  const [name, setName] = useState(athlete.name ?? "");
  const [dateOfBirth, setDateOfBirth] = useState(athlete.date_of_birth ?? "");
  const [sex, setSex] = useState<Sex>(athlete.sex);
  const [heightCm, setHeightCm] = useState(
    athlete.height_cm === null ? "" : String(athlete.height_cm),
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const update = $api.useMutation("patch", "/api/v1/athlete", {
    onSuccess: (updated) => {
      setSaved(true);
      queryClient.setQueryData(athleteQueryKey, updated);
      queryClient.invalidateQueries({ queryKey: athleteQueryKey });
    },
  });

  const problems = [
    ...(problem ? [problem] : []),
    ...apiErrorMessages(update.error),
  ];

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaved(false);
    const height = heightCm.trim() === "" ? null : parseNumberInput(heightCm);
    if (heightCm.trim() !== "" && height === null) {
      setProblem("Height has to be a number of centimetres, or nothing.");
      return;
    }
    setProblem(null);
    // All four fields every time, because `null` is how this API *clears* one
    // (`AthleteUpdate`): sending only what changed would make an emptied field
    // indistinguishable from one the form chose not to mention. `sex` has no
    // null — `unspecified` is its empty value.
    update.mutate({
      body: {
        name: name.trim() === "" ? null : name.trim(),
        date_of_birth: dateOfBirth === "" ? null : dateOfBirth,
        sex,
        height_cm: height,
      },
    });
  }

  return (
    <form className="flex flex-col gap-3.5" onSubmit={submit}>
      <FieldRow>
        <Field
          label="Name"
          htmlFor={`${base}-name`}
          className="flex-[1_1_220px]"
        >
          <Input
            id={`${base}-name`}
            value={name}
            placeholder="How the coach addresses you"
            onChange={(event) => {
              setName(event.target.value);
              setSaved(false);
            }}
          />
        </Field>
        <Field
          label="Date of birth"
          htmlFor={`${base}-dob`}
          className="flex-[1_1_160px]"
        >
          <Input
            id={`${base}-dob`}
            type="date"
            className="font-mono"
            value={dateOfBirth}
            onChange={(event) => {
              setDateOfBirth(event.target.value);
              setSaved(false);
            }}
          />
        </Field>
      </FieldRow>

      <FieldRow>
        <Field label="Sex" htmlFor={`${base}-sex`} className="flex-[1_1_180px]">
          <NativeSelect
            id={`${base}-sex`}
            className="w-full"
            value={sex}
            onChange={(event) => {
              setSex(event.target.value as Sex);
              setSaved(false);
            }}
          >
            {(Object.keys(SEX_LABELS) as Sex[]).map((option) => (
              <NativeSelectOption key={option} value={option}>
                {SEX_LABELS[option]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field
          label="Height"
          hint="cm"
          htmlFor={`${base}-height`}
          className="flex-[1_1_120px]"
        >
          <Input
            id={`${base}-height`}
            inputMode="decimal"
            className="font-mono"
            value={heightCm}
            onChange={(event) => {
              setHeightCm(event.target.value);
              setSaved(false);
            }}
          />
        </Field>
        <Button type="submit" className="ml-auto" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save profile"}
        </Button>
      </FieldRow>

      {saved && problems.length === 0 ? (
        <p role="status" className="text-ink-muted text-sm">
          Profile saved.
        </p>
      ) : null}

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
