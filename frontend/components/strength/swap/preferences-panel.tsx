"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import { $api } from "@/lib/api/client";
import { useI18n } from "@/lib/i18n/provider";

const PREFERENCES = "/api/v1/exercises/{exercise_id}/preferences";
const QUERY_PREFIX = ["get", PREFERENCES];

/**
 * The exercise page's say over swaps: never suggest it, suggest it less
 * often, and *Ease back in* — a short ladder of easier variations, easiest
 * first, that moves one step **only when the athlete taps Next step**
 * (`backend/app/services/exercise_preferences.py`). The page shows the step
 * to train now; the gym screen shows the same note from the packet.
 */
export function PreferencesPanel({ exerciseId }: { exerciseId: string }) {
  const { t } = useI18n();
  const heading = useId();
  const queryClient = useQueryClient();
  const path = { params: { path: { exercise_id: exerciseId } } };
  const { data } = $api.useQuery("get", PREFERENCES, path);
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: QUERY_PREFIX });
  const update = $api.useMutation("patch", PREFERENCES, {
    onSuccess: refresh,
  });
  const advance = $api.useMutation(
    "post",
    "/api/v1/exercises/{exercise_id}/preferences/ease-back/advance",
    { onSuccess: refresh },
  );

  if (!data) return null;
  const ease = data.ease_back;
  const nothingEasier = update.error !== null && update.error !== undefined;

  return (
    <Panel className="flex flex-col gap-3 p-4">
      <section aria-labelledby={heading} className="flex flex-col gap-3">
        <SectionLabel level={2} id={heading}>
          {t("swap.preferences")}
        </SectionLabel>
        {(["avoid", "less_often"] as const).map((field) => (
          <label key={field} className="flex min-h-[44px] items-center gap-3">
            <input
              type="checkbox"
              className="size-5"
              checked={data[field]}
              onChange={(event) =>
                update.mutate({
                  ...path,
                  body: { [field]: event.target.checked },
                })
              }
            />
            {field === "avoid" ? t("swap.avoid") : t("swap.lessOften")}
          </label>
        ))}
        {ease ? (
          <div className="flex flex-col gap-2">
            <p className="font-mono">
              {t("swap.step", {
                n: String(ease.step + 1),
                total: String(ease.steps.length),
                exercise: ease.current.exercise_name,
              })}
            </p>
            <div className="flex gap-2">
              <Button
                className="h-[44px] flex-1"
                onClick={() => advance.mutate(path)}
              >
                {t("swap.nextStep")}
              </Button>
              <Button
                variant="outline"
                className="h-[44px]"
                onClick={() =>
                  update.mutate({ ...path, body: { ease_back: false } })
                }
              >
                {t("swap.stopEaseBack")}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            <Button
              variant="outline"
              className="h-[44px]"
              onClick={() =>
                update.mutate({ ...path, body: { ease_back: true } })
              }
            >
              {t("swap.startEaseBack")}
            </Button>
            {nothingEasier ? (
              <p className="text-ink-muted text-sm">
                {t("swap.nothingEasier")}
              </p>
            ) : null}
          </div>
        )}
      </section>
    </Panel>
  );
}
