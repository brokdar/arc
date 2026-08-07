"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";

import { ConfirmButton, InlineConfirm } from "@/components/design/confirm";
import { Field } from "@/components/design/field";
import { SectionLabel } from "@/components/design/section-label";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { WorkoutBuilder } from "@/components/workouts/workout-builder";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import {
  type Discipline,
  draftFromStructure,
  emptyDraft,
  structureFromDraft,
  validateDraft,
  type WorkoutDraft,
} from "@/lib/workout-draft";

/** Every cached workout list, whatever it was filtered by. */
const WORKOUTS_QUERY_PREFIX = ["get", "/api/v1/workouts"] as const;

export interface WorkoutEditorProps {
  /** `null` for `/workouts/new`; a workout id to edit an existing one. */
  readonly workoutId: string | null;
}

/**
 * The workout creator, and the workout editor — one form.
 *
 * They differ in exactly two ways: where the draft comes from, and which verb
 * saves it. Everything else (the discipline switch, the builder, the live
 * preview, validation, the 422 handling) is the same form, so it is the same
 * component; a "new" mode that is a copy of the "edit" mode is where the two
 * quietly stop agreeing.
 *
 * Unlike the plan dialog this is a *route*, so leaving it means navigating.
 * Both exits are guarded when the draft is unsaved: "← Library" asks first,
 * and `beforeunload` covers the browser's own back button, a typed URL and a
 * closed tab — the three the app cannot intercept.
 */
export function WorkoutEditor({ workoutId }: WorkoutEditorProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const base = useId();
  const editing = workoutId !== null;

  const existing = $api.useQuery(
    "get",
    "/api/v1/workouts/{workout_id}",
    { params: { path: { workout_id: workoutId ?? "" } } },
    { enabled: editing },
  );
  const labels = $api.useQuery("get", "/api/v1/workout-labels");

  const [form, setForm] = useState<FormState | null>(
    editing ? null : blankForm("cycling"),
  );
  const [problems, setProblems] = useState<readonly string[]>([]);
  // Set by the athlete's own edits and cleared by a successful save. A flag
  // rather than a diff against the loaded workout: the draft round-trips
  // through `draftFromStructure`, and comparing two of those would call a
  // pristine form dirty over a normalised empty string.
  const [dirty, setDirty] = useState(false);
  const [leaving, setLeaving] = useState(false);

  // The saved workout arrives once; adopting it during render (rather than in
  // an effect) means the form is never briefly blank after the data is there.
  if (form === null && existing.data) {
    setForm({
      name: existing.data.name,
      description: existing.data.description ?? "",
      folder: existing.data.folder ?? "",
      tags: existing.data.tags.join(", "),
      draft:
        draftFromStructure(existing.data.structure) ??
        emptyDraft(existing.data.discipline),
    });
  }

  /** Every athlete-driven change to the form goes through here. */
  function edit(next: FormState) {
    setForm(next);
    setDirty(true);
  }

  // The one exit the application cannot render a prompt into. The browser
  // shows its own, and only if `preventDefault` is called during the event.
  useEffect(() => {
    if (!dirty) {
      return;
    }
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: WORKOUTS_QUERY_PREFIX });

  const create = $api.useMutation("post", "/api/v1/workouts", {
    onSuccess: (workout) => {
      setDirty(false);
      invalidate();
      router.push(`/workouts/${workout.id}`);
    },
  });
  const update = $api.useMutation("patch", "/api/v1/workouts/{workout_id}", {
    onSuccess: () => {
      setDirty(false);
      invalidate();
      if (workoutId) {
        queryClient.invalidateQueries({
          queryKey: $api.queryOptions("get", "/api/v1/workouts/{workout_id}", {
            params: { path: { workout_id: workoutId } },
          }).queryKey,
        });
      }
    },
  });
  const remove = $api.useMutation("delete", "/api/v1/workouts/{workout_id}", {
    onSuccess: () => {
      setDirty(false);
      invalidate();
      router.push("/workouts");
    },
  });

  const saving = create.isPending || update.isPending;
  const apiProblems = apiErrorMessages(create.error ?? update.error);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form) {
      return;
    }
    const found = [
      ...(form.name.trim() === "" ? ["Give the workout a name."] : []),
      ...validateDraft(form.draft),
    ];
    setProblems(found);
    if (found.length > 0) {
      return;
    }
    const body = {
      name: form.name.trim(),
      description:
        form.description.trim() === "" ? null : form.description.trim(),
      folder: form.folder.trim() === "" ? null : form.folder.trim(),
      tags: splitTags(form.tags),
      structure: structureFromDraft(form.draft),
    };
    if (editing && workoutId) {
      update.mutate({ params: { path: { workout_id: workoutId } }, body });
    } else {
      create.mutate({ body });
    }
  }

  if (editing && existing.error) {
    return (
      <PageBody>
        <p role="alert" className="text-destructive text-sm">
          Could not load this workout.
        </p>
      </PageBody>
    );
  }

  if (!form) {
    return (
      <PageBody>
        <p className="text-ink-muted text-sm">Loading the workout…</p>
      </PageBody>
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <Toolbar>
        {/* An unsaved draft turns the link into a question. `router.push`
            rather than the anchor's own navigation, because the guard has to
            happen before the route changes, not after. */}
        <Button
          variant="ghost"
          size="sm"
          className="text-ink-muted"
          render={
            <Link
              href="/workouts"
              onClick={(event) => {
                if (!dirty) {
                  return;
                }
                event.preventDefault();
                setLeaving(true);
              }}
            >
              ← Library
            </Link>
          }
        />
        {/* The page's one `h1`; the builder's sections sit under it. */}
        <h1 className="font-semibold text-lg tracking-[-0.01em]">
          {editing ? "Edit workout" : "New workout"}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          {editing && workoutId ? (
            // Deleting a workout is not undoable and cannot be told apart
            // from a mis-click on a 7px-tall toolbar. Two clicks.
            <ConfirmButton
              label="Delete"
              question="Delete this workout?"
              confirmLabel="Delete"
              disabled={remove.isPending}
              onConfirm={() =>
                remove.mutate({ params: { path: { workout_id: workoutId } } })
              }
            />
          ) : null}
          <Button type="submit" size="sm" disabled={saving}>
            {saving ? "Saving…" : "Save workout"}
          </Button>
        </div>
      </Toolbar>

      <PageBody className="flex flex-col gap-5">
        {leaving ? (
          <InlineConfirm
            question="Discard this draft and go back to the library?"
            confirmLabel="Discard"
            cancelLabel="Keep editing"
            onConfirm={() => {
              setDirty(false);
              setLeaving(false);
              router.push("/workouts");
            }}
            onCancel={() => setLeaving(false)}
          />
        ) : null}

        {update.isSuccess && !update.isPending ? (
          <p role="status" className="text-status-completed text-sm">
            Saved.
          </p>
        ) : null}

        <section className="flex flex-col gap-3">
          <SectionLabel level={2}>Workout</SectionLabel>
          <div className="flex flex-wrap items-end gap-3">
            <Field
              label="Name"
              htmlFor={`${base}-name`}
              className="min-w-[220px] flex-1"
            >
              <Input
                id={`${base}-name`}
                value={form.name}
                placeholder="VO₂ 5×4′"
                onChange={(event) =>
                  edit({ ...form, name: event.target.value })
                }
              />
            </Field>

            <Field
              label="Discipline"
              htmlFor={`${base}-discipline`}
              className="w-[140px]"
            >
              <NativeSelect
                className="w-full"
                id={`${base}-discipline`}
                value={form.draft.discipline}
                disabled={editing}
                onChange={(event) =>
                  edit({
                    ...form,
                    // Switching discipline replaces the prescription: a step
                    // tree has no meaning as a set of lifts. Disabled while
                    // editing, where it would silently discard saved work.
                    draft: emptyDraft(event.target.value as Discipline),
                  })
                }
              >
                <NativeSelectOption value="cycling">Cycling</NativeSelectOption>
                <NativeSelectOption value="strength">
                  Strength
                </NativeSelectOption>
              </NativeSelect>
            </Field>

            <Field
              label="Folder"
              hint="optional"
              htmlFor={`${base}-folder`}
              className="w-[170px]"
            >
              <Input
                id={`${base}-folder`}
                list={`${base}-folders`}
                value={form.folder}
                onChange={(event) =>
                  edit({ ...form, folder: event.target.value })
                }
              />
              <datalist id={`${base}-folders`}>
                {(labels.data?.folders ?? []).map((folder) => (
                  <option key={folder} value={folder} />
                ))}
              </datalist>
            </Field>

            <Field
              label="Tags"
              hint="comma separated"
              htmlFor={`${base}-tags`}
              className="w-[200px]"
            >
              <Input
                id={`${base}-tags`}
                value={form.tags}
                onChange={(event) =>
                  edit({ ...form, tags: event.target.value })
                }
              />
            </Field>
          </div>

          <Field
            label="Description"
            hint="optional"
            htmlFor={`${base}-description`}
          >
            <Textarea
              id={`${base}-description`}
              rows={2}
              value={form.description}
              onChange={(event) =>
                edit({ ...form, description: event.target.value })
              }
            />
          </Field>
        </section>

        <WorkoutBuilder
          draft={form.draft}
          onChange={(draft) => edit({ ...form, draft })}
          problems={[...problems, ...apiProblems]}
        />
      </PageBody>
    </form>
  );
}

interface FormState {
  readonly name: string;
  readonly description: string;
  readonly folder: string;
  readonly tags: string;
  readonly draft: WorkoutDraft;
}

function blankForm(discipline: Discipline): FormState {
  return {
    name: "",
    description: "",
    folder: "",
    tags: "",
    draft: emptyDraft(discipline),
  };
}

function splitTags(text: string): string[] {
  return text
    .split(",")
    .map((tag) => tag.trim())
    .filter((tag) => tag !== "");
}
