"use client";

import { useId } from "react";

import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { $api } from "@/lib/api/client";
import { useI18n } from "@/lib/i18n/provider";

/** The rep counts the backend's table reports (`REP_MAX_TARGETS`). */
const REP_SLOTS = [1, 3, 5, 8, 10] as const;

/**
 * One exercise's history: the estimated max, the heaviest weight for each
 * rep count, and every personal best.
 *
 * The estimate sits in its own panel with its caveat, apart from the bests,
 * because it is a guide computed from how hard sets felt and a best is a set
 * that was lifted — the page must not let one read as the other. The
 * rep-count grid keeps all five slots (UI convention 4); a count never
 * reached holds a `NotAssessed` saying so.
 *
 * Weights are shown in kilograms, as the history stores them.
 */
export function ExerciseHistory({ exerciseId }: { exerciseId: string }) {
  const { t, weight } = useI18n();
  const ids = { estimate: useId(), grid: useId(), bests: useId() };
  const { data, error, isPending } = $api.useQuery(
    "get",
    "/api/v1/exercises/{exercise_id}/history",
    { params: { path: { exercise_id: exerciseId } } },
  );

  if (isPending) {
    return <p className="p-4 text-ink-muted">{t("records.historyLoading")}</p>;
  }
  if (error || !data) {
    return <p className="p-4 text-ink-muted">{t("records.historyMissing")}</p>;
  }

  const { estimate } = data;
  const set = (load: number, reps: number) =>
    t("records.bestLine", { weight: weight(load), reps });

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-col gap-1">
        <h1 className="font-semibold text-2xl">{data.exercise_name}</h1>
        <p className="font-mono text-ink-muted text-sm">
          {t("records.sessions", { count: data.exposures })}
        </p>
      </header>

      <Panel className="flex flex-col gap-1 p-4">
        <section aria-labelledby={ids.estimate} className="flex flex-col gap-1">
          <SectionLabel level={2} id={ids.estimate}>
            {t("records.estimateTitle")}
          </SectionLabel>
          {estimate.status === "ok" && estimate.current_kg !== null ? (
            <>
              <p className="font-mono text-2xl">
                {t("records.estimateValue", {
                  weight: weight(estimate.current_kg),
                })}
              </p>
              <p className="text-ink-muted text-xs">
                {t("records.estimateNote")}
              </p>
            </>
          ) : (
            <>
              <p className="text-lg">{t("records.notEnoughData")}</p>
              <p className="text-ink-muted text-xs">
                {t("records.notEnoughDataHelp")}
              </p>
            </>
          )}
        </section>
      </Panel>

      {data.exposures === 0 ? (
        <p className="text-ink-muted">{t("records.historyEmpty")}</p>
      ) : null}

      <Panel className="p-4">
        <section aria-labelledby={ids.grid} className="flex flex-col gap-2">
          <SectionLabel level={2} id={ids.grid}>
            {t("records.repMaxesTitle")}
          </SectionLabel>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {REP_SLOTS.map((reps) => {
              const label = t("records.repMaxLabel", { reps });
              const reached = data.rep_maxes.find(
                (m) => m.target_reps === reps,
              );
              return (
                // biome-ignore lint/a11y/useSemanticElements: a fieldset would restyle the grid; role=group names the slot the same way
                <div
                  key={reps}
                  role="group"
                  aria-label={label}
                  className="flex flex-col gap-0.5"
                >
                  <span className="text-ink-muted text-xs">{label}</span>
                  {reached ? (
                    <span className="font-mono">
                      {set(reached.load_kg, reached.reps)}
                    </span>
                  ) : (
                    <NotAssessed
                      reason={t("records.repMaxMissing", { reps })}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </section>
      </Panel>

      {data.bests.length > 0 ? (
        <Panel className="p-4">
          <section aria-labelledby={ids.bests} className="flex flex-col gap-2">
            <SectionLabel level={2} id={ids.bests}>
              {t("records.bestsListTitle")}
            </SectionLabel>
            <ul className="flex flex-col gap-1">
              {data.bests.map((best) => (
                <li
                  key={`${best.load_kg}:${best.reps}`}
                  className="flex justify-between gap-4 font-mono"
                >
                  <span>{set(best.load_kg, best.reps)}</span>
                  <span className="text-ink-muted text-sm">{best.date}</span>
                </li>
              ))}
            </ul>
          </section>
        </Panel>
      ) : null}
    </div>
  );
}
