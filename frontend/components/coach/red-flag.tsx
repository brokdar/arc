"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Field } from "@/components/design/field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import type { MessageKey } from "@/lib/i18n/catalogue";
import { useT } from "@/lib/i18n/provider";

type Severity = components["schemas"]["RedFlagSeverity"];

const SEVERITY_LABELS: Readonly<Record<Severity, MessageKey>> = {
  mild: "illness.mildLong",
  moderate: "illness.moderateLong",
  severe: "illness.severeLong",
};

/** The word on the banner, without the explanation the form's option carries. */
const SEVERITY_WORDS: Readonly<Record<Severity, MessageKey>> = {
  mild: "illness.mild",
  moderate: "illness.moderate",
  severe: "illness.severe",
};

const athleteQueryKey = $api.queryOptions("get", "/api/v1/athlete").queryKey;

/**
 * The athlete's illness/injury flag: read it, and set it.
 *
 * One flag, and it is a *guardrail* rather than a note — while it stands, the
 * service layer refuses every agent proposal that adds or intensifies a
 * session (WP-8 §4). That is why the banner below is loud and unconditional:
 * a refusal in force that the athlete has forgotten about looks exactly like
 * a coach that has stopped suggesting anything.
 */
function useRedFlag() {
  const queryClient = useQueryClient();
  const { data } = $api.useQuery("get", "/api/v1/athlete");
  const update = $api.useMutation("patch", "/api/v1/athlete", {
    onSuccess: (athlete) => {
      queryClient.setQueryData(athleteQueryKey, athlete);
      queryClient.invalidateQueries({ queryKey: athleteQueryKey });
    },
  });
  return { athlete: data, update };
}

/**
 * The banner the whole application wears while a red flag is up.
 *
 * In the shell rather than on a page, because the flag is not a property of
 * any one screen: it changes what the coach is allowed to propose everywhere,
 * and a control the athlete has to remember to go and look at is a control
 * they will forget they set. It renders nothing at all while the flag
 * is down, so the layout of every page is unchanged in the normal case.
 */
export function RedFlagBanner() {
  const { athlete, update } = useRedFlag();
  const t = useT();
  const [editing, setEditing] = useState(false);

  if (!athlete?.red_flag_active) {
    return null;
  }

  const severity = athlete.red_flag_severity;
  // "All better" is a write like any other, and it is the one write in the app
  // with no form behind it to carry the refusal. A failed PATCH that printed
  // nothing here left the banner standing with no explanation — which reads as
  // a button that does not work, and the athlete's next move is to press it
  // again.
  const problems = apiErrorMessages(update.error);

  return (
    <>
      <div
        role="status"
        data-testid="red-flag-banner"
        className="flex flex-wrap items-center gap-x-3 gap-y-1 border-danger-border border-b bg-danger-surface px-[22px] py-2"
      >
        <span
          aria-hidden
          className="size-1.5 shrink-0 rounded-full bg-status-missed"
        />
        <span className="font-medium text-destructive text-sm">
          {severity
            ? t("illness.bannerSeverity", {
                severity: t(SEVERITY_WORDS[severity]),
              })
            : t("illness.banner")}
        </span>
        <span className="text-ink-secondary text-xs">
          {athlete.red_flag_note ?? t("illness.noNote")}
        </span>
        <span className="text-ink-muted text-xs">
          {t("illness.coachCannot")}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <Button
            size="xs"
            variant="secondary"
            onClick={() => setEditing(true)}
          >
            {t("illness.change")}
          </Button>
          <Button
            size="xs"
            disabled={update.isPending}
            // Lowering it sends only the flag: the API clears the note and the
            // severity itself, and sending nulls for them here would be this
            // form asserting a rule the service already owns.
            onClick={() => update.mutate({ body: { red_flag_active: false } })}
          >
            {update.isPending ? t("illness.clearing") : t("illness.allBetter")}
          </Button>
        </span>
        {problems.length > 0 ? (
          <ul
            role="alert"
            className="flex w-full flex-col gap-0.5 text-destructive text-xs"
          >
            {problems.map((entry) => (
              <li key={entry}>{entry}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {editing ? <RedFlagDialog onClose={() => setEditing(false)} /> : null}
    </>
  );
}

/**
 * The control that raises the flag, on the page about how today is going.
 *
 * Today rather than the calendar, because "I am ill" is a statement about
 * right now and the calendar is where weeks are arranged. It shows the
 * current state either way — an athlete who cannot see that the flag is down
 * cannot tell "the coach is quiet" from "the coach is muzzled".
 */
export function RedFlagControl() {
  const { athlete } = useRedFlag();
  const t = useT();
  const [editing, setEditing] = useState(false);

  if (athlete === undefined) {
    return null;
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className={
          athlete.red_flag_active ? "text-destructive" : "text-ink-muted"
        }
        onClick={() => setEditing(true)}
      >
        {athlete.red_flag_active ? t("illness.up") : t("illness.report")}
      </Button>
      {editing ? <RedFlagDialog onClose={() => setEditing(false)} /> : null}
    </>
  );
}

/**
 * Raise, revise or lower the flag.
 *
 * Two rules from the API are mirrored here rather than only enforced there.
 * A flag that is up **must** carry a severity — "something is wrong" with no
 * indication of how wrong is not enough for a guardrail to act on, so the save
 * is refused in front of the athlete instead of bouncing off a 422. And
 * lowering the flag clears the note and the severity: they described an
 * illness that is over, and keeping them would leave the next flag inheriting
 * the last one's story.
 */
function RedFlagDialog({ onClose }: { onClose: () => void }) {
  const base = useId();
  const { athlete, update } = useRedFlag();
  const t = useT();
  const [active, setActive] = useState(athlete?.red_flag_active ?? false);
  const [severity, setSeverity] = useState<Severity | "">(
    athlete?.red_flag_severity ?? "",
  );
  const [note, setNote] = useState(athlete?.red_flag_note ?? "");
  const [problem, setProblem] = useState<string | null>(null);

  const problems = [
    ...(problem ? [problem] : []),
    ...apiErrorMessages(update.error),
  ];

  function save() {
    // Same reason as the two forms on the settings page: react-query keeps the
    // last refusal until the next `mutate()`, so returning early below would
    // show it beside the fresh complaint.
    update.reset();
    if (active && severity === "") {
      setProblem(t("illness.sayHowBad"));
      return;
    }
    setProblem(null);
    update.mutate(
      {
        body: active
          ? {
              red_flag_active: true,
              red_flag_severity: severity as Severity,
              red_flag_note: note.trim() === "" ? null : note.trim(),
            }
          : { red_flag_active: false },
      },
      { onSuccess: onClose },
    );
  }

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent
        className="w-[min(34rem,calc(100vw-2rem))] max-w-none gap-0 rounded-shell border border-hairline-card bg-panel p-0 ring-0"
        aria-label={t("illness.dialogTitle")}
      >
        <DialogHeader className="gap-1 border-hairline border-b px-6 py-4">
          <DialogTitle className="font-semibold text-ink text-xl tracking-[-0.02em]">
            {t("illness.dialogTitle")}
          </DialogTitle>
          <DialogDescription className="text-ink-muted text-sm">
            {t("illness.dialogBody")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 px-6 py-5">
          <label className="flex items-start gap-2.5 text-base text-ink-secondary">
            <input
              type="checkbox"
              className="mt-1 size-3.5 shrink-0 accent-accent"
              checked={active}
              onChange={(event) => {
                const next = event.target.checked;
                setActive(next);
                setProblem(null);
                if (!next) {
                  // Mirrors what the API does on the way down, so the form
                  // never shows a severity for a flag that is not up.
                  setSeverity("");
                  setNote("");
                }
              }}
            />
            <span>
              {t("illness.active")}
              <span className="block text-ink-muted text-xs">
                {t("illness.activeHelp")}
              </span>
            </span>
          </label>

          <Field
            label={t("illness.severityLabel")}
            hint={
              active
                ? t("illness.severityRequired")
                : t("illness.severityWhenUp")
            }
            htmlFor={`${base}-severity`}
          >
            <NativeSelect
              id={`${base}-severity`}
              className="w-full"
              disabled={!active}
              value={severity}
              onChange={(event) => {
                setSeverity(event.target.value as Severity | "");
                setProblem(null);
              }}
            >
              <NativeSelectOption value="">
                {t("illness.chooseOne")}
              </NativeSelectOption>
              {(Object.keys(SEVERITY_LABELS) as Severity[]).map((value) => (
                <NativeSelectOption key={value} value={value}>
                  {t(SEVERITY_LABELS[value])}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>

          <Field
            label={t("illness.noteLabel")}
            hint={t("illness.noteHint")}
            htmlFor={`${base}-note`}
          >
            <Textarea
              id={`${base}-note`}
              rows={3}
              disabled={!active}
              value={note}
              placeholder={t("illness.notePlaceholder")}
              onChange={(event) => setNote(event.target.value)}
            />
          </Field>

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
        </div>

        <div className="flex items-center gap-2 border-hairline border-t px-6 py-3.5">
          <Button
            variant="ghost"
            size="sm"
            className="text-ink-muted"
            onClick={onClose}
          >
            {t("illness.cancel")}
          </Button>
          <Button
            size="sm"
            className="ml-auto"
            disabled={update.isPending}
            onClick={save}
          >
            {update.isPending
              ? t("illness.saving")
              : active
                ? t("illness.raise")
                : t("illness.save")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
