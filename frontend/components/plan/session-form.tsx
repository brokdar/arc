"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { DiscardPrompt, useDirtyClose } from "@/components/design/dirty-close";
import { Field } from "@/components/design/field";
import { PurposeBadge } from "@/components/design/purpose-badge";
import { SectionLabel } from "@/components/design/section-label";
import { CriteriaEditor } from "@/components/plan/criteria-editor";
import { WorkoutPicker } from "@/components/plan/workout-picker";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { WorkoutBuilder } from "@/components/workouts/workout-builder";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import type { SuccessCriterion } from "@/lib/criteria";
import {
  disciplineOfPurpose,
  type Purpose,
  purposeLabel,
  purposesFor,
} from "@/lib/purpose";
import {
  draftFromStructure,
  emptyDraft,
  structureFromDraft,
  validateDraft,
  type WorkoutDraft,
} from "@/lib/workout-draft";

type Schemas = components["schemas"];

/** Every cached week, whichever `start` it was fetched with. */
const WEEK_QUERY_PREFIX = ["get", "/api/v1/plan/week"] as const;

export interface SessionFormProps {
  /** The day the athlete clicked; pre-fills the date on a new session. */
  readonly date: string;
  /** Set to edit an existing session; omitted to plan a new one. */
  readonly sessionId?: string | null;
  readonly onClose: () => void;
  readonly onSaved?: () => void;
}

/**
 * Plan a session, or revise one — one form, two verbs.
 *
 * Two things here are not obvious and are deliberate:
 *
 * **Criteria follow the purpose until they don't.** Opening the form for a
 * purpose loads that purpose's template criteria; changing the purpose
 * re-derives them. The moment the athlete edits, adds or removes one, the list
 * stops following — because from then on it is *their* rule, and silently
 * replacing it on the next purpose change would throw away an edit they made
 * on purpose.
 *
 * **A PATCH sends only what changed.** The backend refuses a body carrying
 * both `workout_id` and `structure` (a prescription has one source) and treats
 * any intent field as a new intent version. Sending the whole form on every
 * save would version the intent for a date change and trip that rule for free,
 * so the edit path diffs against what it loaded.
 *
 * **Nothing here closes silently.** A stray click on the backdrop used to
 * discard a half-written session without a word; now anything the athlete
 * typed makes the dialog ask first (`useDirtyClose`). And the form does not
 * offer to save while the purpose's template is still in flight: submitting
 * then would post `success_criteria: []` and freeze a session judged by
 * nothing, which is indistinguishable afterwards from having chosen that.
 */
export function SessionForm({
  date,
  sessionId = null,
  onClose,
  onSaved,
}: SessionFormProps) {
  const base = useId();
  const queryClient = useQueryClient();
  const editing = sessionId !== null;

  const existing = $api.useQuery(
    "get",
    "/api/v1/planned-sessions/{planned_session_id}",
    { params: { path: { planned_session_id: sessionId ?? "" } } },
    { enabled: editing },
  );

  const [form, setForm] = useState<FormState | null>(
    editing ? null : blankForm(date),
  );
  const [loaded, setLoaded] = useState<FormState | null>(null);
  const [problems, setProblems] = useState<readonly string[]>([]);
  // Bumped whenever the criteria list is replaced wholesale, so the editor's
  // uncontrolled number fields remount with the new values in them.
  const [criteriaKey, setCriteriaKey] = useState(0);
  // Set by the athlete's own edits and by nothing else. A flag rather than a
  // diff against an initial value, because the template fills the criteria on
  // its own a moment after the dialog opens: a diff would call an untouched
  // form dirty and prompt to discard a draft nobody wrote.
  const [dirty, setDirty] = useState(false);

  if (form === null && existing.data) {
    const initial = formFromSession(existing.data);
    setForm(initial);
    setLoaded(initial);
  }

  /** Every athlete-driven change to the form goes through here. */
  function edit(next: FormState) {
    setForm(next);
    setDirty(true);
  }

  const purpose = form?.purpose ?? "endurance";
  const discipline = disciplineOfPurpose(purpose);

  const template = $api.useQuery("get", "/api/v1/purposes/{purpose}", {
    params: { path: { purpose } },
  });

  // The template is the *default*, not the value: it fills the list only while
  // the athlete has left it alone. `criteriaTouched` is what stops a purpose
  // change from overwriting an edited list.
  const defaults = template.data?.default_criteria;
  const untouched = form !== null && !form.criteriaTouched;
  useEffect(() => {
    if (!untouched || !defaults) {
      return;
    }
    setForm((current) =>
      current && !current.criteriaTouched
        ? { ...current, criteria: defaults }
        : current,
    );
    setCriteriaKey((key) => key + 1);
  }, [defaults, untouched]);

  const invalidateWeeks = () => {
    queryClient.invalidateQueries({ queryKey: WEEK_QUERY_PREFIX });
    if (sessionId) {
      queryClient.invalidateQueries({
        queryKey: $api.queryOptions(
          "get",
          "/api/v1/planned-sessions/{planned_session_id}",
          { params: { path: { planned_session_id: sessionId } } },
        ).queryKey,
      });
    }
  };

  const create = $api.useMutation("post", "/api/v1/planned-sessions", {
    onSuccess: () => {
      invalidateWeeks();
      onSaved?.();
      onClose();
    },
  });
  const update = $api.useMutation(
    "patch",
    "/api/v1/planned-sessions/{planned_session_id}",
    {
      onSuccess: () => {
        invalidateWeeks();
        onSaved?.();
        onClose();
      },
    },
  );

  const saving = create.isPending || update.isPending;
  const apiProblems = apiErrorMessages(create.error ?? update.error);

  /**
   * The criteria are still on their way, and posting now would post none.
   *
   * Only while the list is the template's: once the athlete has touched it,
   * whatever it holds is their answer and no request is owed. The template's
   * *failure* deliberately does not block — an empty list is then a choice
   * made in front of a message saying so, rather than one made silently.
   */
  const awaitingTemplate =
    form !== null && !form.criteriaTouched && template.isPending;
  const templateProblems = template.isError
    ? [
        "Could not load this purpose's template, so no criteria were pre-filled. Add them yourself, or plan the session judged by completion alone.",
      ]
    : [];

  function setPurpose(next: Purpose) {
    if (!form) {
      return;
    }
    const nextDiscipline = disciplineOfPurpose(next);
    if (nextDiscipline === disciplineOfPurpose(form.purpose)) {
      edit({ ...form, purpose: next });
      return;
    }
    // The discipline changed under the prescription: a step tree is not a set
    // of lifts, and a cycling workout cannot be planned for a strength
    // purpose. Start that half over rather than send something the domain
    // will refuse.
    edit({
      ...form,
      purpose: next,
      workoutId: null,
      draft: emptyDraft(nextDiscipline),
    });
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form) {
      return;
    }
    const found =
      form.source === "inline"
        ? validateDraft(form.draft)
        : sourceProblems(form);
    setProblems(found);
    if (found.length > 0) {
      return;
    }

    if (editing && sessionId && loaded) {
      const body = diff(loaded, form);
      if (Object.keys(body).length === 0) {
        onClose();
        return;
      }
      update.mutate({
        params: { path: { planned_session_id: sessionId } },
        body,
      });
      return;
    }

    create.mutate({
      body: {
        date: form.date,
        purpose: form.purpose,
        intent_text: emptyToNull(form.intentText),
        coach_notes: emptyToNull(form.coachNotes),
        success_criteria: [...form.criteria],
        ...(form.source === "library"
          ? { workout_id: form.workoutId }
          : { structure: structureFromDraft(form.draft) }),
      },
    });
  }

  const allProblems = [...problems, ...templateProblems, ...apiProblems];
  const guard = useDirtyClose({ dirty, onClose });

  return (
    <Dialog open onOpenChange={guard.onOpenChange}>
      <DialogContent
        className="max-h-[88vh] w-[min(52rem,calc(100vw-2rem))] max-w-none overflow-y-auto gap-0 rounded-shell border border-hairline-card bg-panel p-0 ring-0"
        aria-label={editing ? "Edit session" : "Plan a session"}
      >
        <form onSubmit={handleSubmit} className="flex flex-col">
          <DialogHeader className="gap-1 border-hairline border-b px-6 py-4">
            <DialogTitle className="font-semibold text-ink text-xl tracking-[-0.02em]">
              {editing ? "Edit session" : "Plan a session"}
            </DialogTitle>
            <DialogDescription className="text-ink-muted text-sm">
              {editing
                ? "Editing what a session is for writes a new intent version; moving it does not."
                : "The prescription is frozen at the anchors in force today."}
            </DialogDescription>
          </DialogHeader>

          {!form ? (
            <p className="px-6 py-6 text-ink-muted text-sm">
              Loading the session…
            </p>
          ) : (
            <div className="flex flex-col gap-5 px-6 py-5">
              <div className="flex flex-wrap items-end gap-3">
                <Field
                  label="Date"
                  htmlFor={`${base}-date`}
                  className="w-[150px]"
                >
                  <Input
                    id={`${base}-date`}
                    type="date"
                    className="font-mono"
                    value={form.date}
                    onChange={(event) =>
                      edit({ ...form, date: event.target.value })
                    }
                  />
                </Field>

                <Field
                  label="Purpose"
                  htmlFor={`${base}-purpose`}
                  className="w-[190px]"
                >
                  <NativeSelect
                    className="w-full"
                    id={`${base}-purpose`}
                    value={form.purpose}
                    onChange={(event) =>
                      setPurpose(event.target.value as Purpose)
                    }
                  >
                    <optgroup label="Cycling">
                      {purposesFor("cycling").map((value) => (
                        <NativeSelectOption key={value} value={value}>
                          {purposeLabel(value)}
                        </NativeSelectOption>
                      ))}
                    </optgroup>
                    <optgroup label="Strength">
                      {purposesFor("strength").map((value) => (
                        <NativeSelectOption key={value} value={value}>
                          {purposeLabel(value)}
                        </NativeSelectOption>
                      ))}
                    </optgroup>
                  </NativeSelect>
                </Field>

                <PurposeBadge
                  purpose={form.purpose}
                  size="md"
                  className="mb-2"
                />
              </div>

              <section className="flex flex-col gap-2.5">
                <div className="flex items-center gap-3">
                  <SectionLabel level={3} className="mr-auto">
                    Prescription
                  </SectionLabel>
                  <div className="flex gap-1">
                    <Button
                      type="button"
                      size="xs"
                      variant={
                        form.source === "library" ? "secondary" : "ghost"
                      }
                      aria-pressed={form.source === "library"}
                      className="text-ink-secondary"
                      onClick={() => edit({ ...form, source: "library" })}
                    >
                      From the library
                    </Button>
                    <Button
                      type="button"
                      size="xs"
                      variant={form.source === "inline" ? "secondary" : "ghost"}
                      aria-pressed={form.source === "inline"}
                      className="text-ink-secondary"
                      onClick={() => edit({ ...form, source: "inline" })}
                    >
                      Describe it here
                    </Button>
                  </div>
                </div>

                {form.source === "library" ? (
                  <WorkoutPicker
                    discipline={discipline}
                    value={form.workoutId}
                    onChange={(workout) =>
                      edit({ ...form, workoutId: workout?.id ?? null })
                    }
                  />
                ) : (
                  <WorkoutBuilder
                    draft={form.draft}
                    onChange={(draft) => edit({ ...form, draft })}
                  />
                )}
              </section>

              <Field label="Intent" htmlFor={`${base}-intent`}>
                <Textarea
                  id={`${base}-intent`}
                  rows={2}
                  placeholder="Why this session exists, in one line."
                  value={form.intentText}
                  onChange={(event) =>
                    edit({ ...form, intentText: event.target.value })
                  }
                />
              </Field>

              <Field
                label="Notes to self"
                hint="what to watch for"
                htmlFor={`${base}-notes`}
              >
                <Textarea
                  id={`${base}-notes`}
                  rows={2}
                  value={form.coachNotes}
                  onChange={(event) =>
                    edit({ ...form, coachNotes: event.target.value })
                  }
                />
              </Field>

              <section className="flex flex-col gap-2">
                <SectionLabel level={3}>Success criteria</SectionLabel>
                <CriteriaEditor
                  key={criteriaKey}
                  discipline={discipline}
                  criteria={form.criteria}
                  fromTemplate={!form.criteriaTouched}
                  onChange={(criteria) =>
                    edit({ ...form, criteria, criteriaTouched: true })
                  }
                  onResetToTemplate={() => {
                    setForm({
                      ...form,
                      criteria: defaults ?? [],
                      criteriaTouched: false,
                    });
                    setCriteriaKey((key) => key + 1);
                  }}
                />
              </section>

              {allProblems.length > 0 ? (
                <ul
                  role="alert"
                  className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
                >
                  {allProblems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          )}

          {guard.confirming ? (
            <DiscardPrompt
              what="this draft"
              onDiscard={guard.discard}
              onKeepEditing={guard.keepEditing}
              className="mx-6 mb-4"
            />
          ) : null}

          <div className="sticky bottom-0 flex items-center gap-2 border-hairline border-t bg-panel px-6 py-3.5">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-ink-muted"
              onClick={guard.requestClose}
            >
              Cancel
            </Button>
            {awaitingTemplate ? (
              <span className="ml-auto mr-2 text-ink-muted text-xs">
                Loading this purpose's criteria template…
              </span>
            ) : null}
            <Button
              type="submit"
              size="sm"
              className={awaitingTemplate ? undefined : "ml-auto"}
              disabled={saving || !form || awaitingTemplate}
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Plan it"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface FormState {
  readonly date: string;
  readonly purpose: Purpose;
  readonly source: "library" | "inline";
  readonly workoutId: string | null;
  readonly draft: WorkoutDraft;
  readonly intentText: string;
  readonly coachNotes: string;
  readonly criteria: readonly SuccessCriterion[];
  /** Once true, the purpose no longer re-derives the criteria. */
  readonly criteriaTouched: boolean;
}

function blankForm(date: string): FormState {
  return {
    date,
    purpose: "endurance",
    source: "library",
    workoutId: null,
    draft: emptyDraft("cycling"),
    intentText: "",
    coachNotes: "",
    criteria: [],
    criteriaTouched: false,
  };
}

function formFromSession(session: Schemas["PlannedSessionRead"]): FormState {
  const intent = session.intent;
  return {
    date: session.date,
    purpose: intent.purpose,
    source: intent.workout_id ? "library" : "inline",
    workoutId: intent.workout_id,
    draft:
      draftFromStructure(intent.structure) ?? emptyDraft(session.discipline),
    intentText: intent.intent_text ?? "",
    coachNotes: intent.coach_notes ?? "",
    criteria: intent.success_criteria,
    // A saved session's criteria are its own, whatever they started as.
    criteriaTouched: true,
  };
}

function sourceProblems(form: FormState): string[] {
  return form.workoutId ? [] : ["Choose a workout, or describe one here."];
}

/**
 * What actually changed, as a PATCH body.
 *
 * Deliberately conservative about the prescription: it is sent only when the
 * source or the document changed, so re-saving a session to fix a typo in the
 * notes does not re-pin its anchors.
 */
function diff(
  before: FormState,
  after: FormState,
): Schemas["PlannedSessionUpdate"] {
  const body: Schemas["PlannedSessionUpdate"] = {};
  if (after.date !== before.date) {
    body.date = after.date;
  }
  if (after.purpose !== before.purpose) {
    body.purpose = after.purpose;
  }
  if (after.intentText !== before.intentText) {
    body.intent_text = emptyToNull(after.intentText);
  }
  if (after.coachNotes !== before.coachNotes) {
    body.coach_notes = emptyToNull(after.coachNotes);
  }
  if (JSON.stringify(after.criteria) !== JSON.stringify(before.criteria)) {
    body.success_criteria = [...after.criteria];
  }
  if (after.source === "library") {
    if (after.workoutId !== before.workoutId || before.source !== "library") {
      body.workout_id = after.workoutId;
    }
    return body;
  }
  const next = structureFromDraft(after.draft);
  if (
    before.source !== "inline" ||
    JSON.stringify(next) !== JSON.stringify(structureFromDraft(before.draft))
  ) {
    body.structure = next;
  }
  return body;
}

function emptyToNull(text: string): string | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : trimmed;
}
