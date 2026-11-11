"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Field } from "@/components/design/field";
import { Button } from "@/components/ui/button";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";

type Plan = components["schemas"]["PlanRead"];
type PlanPhase = components["schemas"]["PlanPhase"];

const PHASE_LABELS: Record<PlanPhase, string> = {
  base: "Base",
  build: "Build",
  peak: "Peak",
  taper: "Taper",
  maintenance: "Maintenance",
  transition: "Transition",
};

const plansQueryKey = $api.queryOptions("get", "/api/v1/plans").queryKey;

/**
 * A plan's phase and whether it is followed exactly.
 *
 * The default plan is offered first: it is the plan of every date no dated
 * plan covers, so for an athlete who never made a plan it is *the* plan. A
 * save sends only what changed, so saving an untouched panel is no request
 * and leaves no audit row.
 */
export function PlanSettings() {
  const base = useId();
  const plans = $api.useQuery("get", "/api/v1/plans");
  const ordered = [...(plans.data ?? [])].sort(
    (a, b) => Number(b.is_default) - Number(a.is_default),
  );
  const [chosenId, setChosenId] = useState<string | null>(null);
  const chosen =
    ordered.find((plan) => plan.id === chosenId) ?? ordered[0] ?? null;

  if (plans.isPending) {
    return <p className="text-ink-muted text-sm">Loading your plans…</p>;
  }
  if (!chosen) {
    return null;
  }

  return (
    <section className="flex flex-col gap-3" aria-label="Plan settings">
      <Field label="Plan" htmlFor={`${base}-plan`} className="w-[220px]">
        <NativeSelect
          id={`${base}-plan`}
          className="w-full"
          value={chosen.id}
          onChange={(event) => setChosenId(event.target.value)}
        >
          {ordered.map((plan) => (
            <NativeSelectOption key={plan.id} value={plan.id}>
              {plan.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Field>
      {/* Keyed by plan: switching plans starts from that plan's saved values. */}
      <PlanForm key={`${chosen.id}-${chosen.updated_at}`} plan={chosen} />
    </section>
  );
}

function PlanForm({ plan }: { readonly plan: Plan }) {
  const base = useId();
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<PlanPhase>(plan.phase);
  const [followExactly, setFollowExactly] = useState(plan.follow_exactly);
  const [saved, setSaved] = useState(false);

  const update = $api.useMutation("patch", "/api/v1/plans/{plan_id}", {
    onSuccess: () => {
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: plansQueryKey });
    },
  });
  const problems = apiErrorMessages(update.error);

  function save() {
    const body: components["schemas"]["PlanUpdate"] = {};
    if (phase !== plan.phase) {
      body.phase = phase;
    }
    if (followExactly !== plan.follow_exactly) {
      body.follow_exactly = followExactly;
    }
    if (Object.keys(body).length === 0) {
      return;
    }
    setSaved(false);
    update.mutate({ params: { path: { plan_id: plan.id } }, body });
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="Phase" htmlFor={`${base}-phase`} className="w-[220px]">
        <NativeSelect
          id={`${base}-phase`}
          className="w-full"
          value={phase}
          onChange={(event) => {
            setPhase(event.target.value as PlanPhase);
            setSaved(false);
          }}
        >
          {(Object.keys(PHASE_LABELS) as PlanPhase[]).map((value) => (
            <NativeSelectOption key={value} value={value}>
              {PHASE_LABELS[value]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Field>
      <label className="flex items-start gap-2.5 text-ink-secondary text-sm">
        <input
          type="checkbox"
          className="mt-1 size-3.5 shrink-0 accent-accent"
          checked={followExactly}
          onChange={(event) => {
            setFollowExactly(event.target.checked);
            setSaved(false);
          }}
        />
        <span>
          Follow exactly
          <span className="block text-ink-muted">
            Do the sessions as written — for a programme someone else set, like
            a physio&apos;s. arc will not adapt them.
          </span>
        </span>
      </label>
      {problems.length > 0 ? (
        <ul role="alert" className="text-destructive text-sm">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          size="sm"
          disabled={update.isPending}
          onClick={save}
        >
          {update.isPending ? "Saving…" : "Save plan"}
        </Button>
        {saved ? (
          <span role="status" className="text-ink-muted text-xs">
            Saved
          </span>
        ) : null}
      </div>
    </div>
  );
}
