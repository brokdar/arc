"use client";

import { useQueryClient } from "@tanstack/react-query";

import { PauseIcon, PlayIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";

type PlanState = components["schemas"]["PlanState"];

const athleteQueryKey = $api.queryOptions("get", "/api/v1/athlete").queryKey;

/**
 * Reads and writes `athlete.plan_state` (D58).
 *
 * Pausing does not pause the application: ingestion, matching and scoring
 * carry on. What stops is missed-session marking — so the UI has to say what
 * it means rather than just showing a switch.
 */
function usePlanState() {
  const queryClient = useQueryClient();
  const { data } = $api.useQuery("get", "/api/v1/athlete");
  const update = $api.useMutation("patch", "/api/v1/athlete", {
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: athleteQueryKey }),
  });

  return {
    planState: data?.plan_state,
    pending: update.isPending,
    setPlanState: (plan_state: PlanState) =>
      update.mutate({ body: { plan_state } }),
  };
}

/** The banner that appears above the week while the plan is paused. */
export function PlanStateBanner() {
  const { planState, pending, setPlanState } = usePlanState();

  if (planState !== "paused") {
    return null;
  }

  return (
    <div
      role="status"
      className="mb-4 flex flex-wrap items-center gap-3 rounded-card border border-warn-border bg-warn-surface px-3.5 py-2.5"
    >
      <span
        aria-hidden
        className="size-1.5 shrink-0 rounded-full bg-status-under"
      />
      <span className="font-medium text-sm text-status-under">Plan paused</span>
      <span className="text-ink-muted text-xs">
        Activities still sync and score; sessions you skip are not marked
        missed.
      </span>
      <Button
        size="sm"
        className="ml-auto"
        disabled={pending}
        onClick={() => setPlanState("active")}
      >
        <PlayIcon />
        Resume plan
      </Button>
    </div>
  );
}

/** The unobtrusive pause/resume control in the calendar toolbar. */
export function PlanStateToggle() {
  const { planState, pending, setPlanState } = usePlanState();

  if (planState === undefined) {
    return null;
  }
  const paused = planState === "paused";

  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={pending}
      onClick={() => setPlanState(paused ? "active" : "paused")}
      className="text-ink-muted"
    >
      {paused ? <PlayIcon /> : <PauseIcon />}
      {paused ? "Resume plan" : "Pause plan"}
    </Button>
  );
}
