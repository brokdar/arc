"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Panel } from "@/components/design/panel";
import { Button } from "@/components/ui/button";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { useT } from "@/lib/i18n/provider";
import { trainingSettingsQueryKey } from "@/lib/training-settings";

type Update = components["schemas"]["TrainingSettingsUpdate"];

/**
 * The before-you-start screen: the safety essentials, shown once.
 *
 * What to do about sharp pain and about a red flag — both reachable from ⋯
 * on the gym screen — and that arc is a log, not medical advice. "Seen" is a
 * training setting (`before_you_start_seen`), so it is shown once per
 * athlete, not once per phone; *Got it* and *Skip* both store it, because
 * "skip" answers "show me this again?" with no.
 *
 * **"I'm under 18" sets `no_max_efforts`** and shows why at once. It is sent
 * with whichever button closes the screen, Skip included — a young lifter who
 * ticks the box and then skips has still told arc their age — and only ever
 * as `true`: an unticked box says nothing, so it never clears a flag set
 * before.
 *
 * Like the first-run question it renders nothing until the settings have
 * loaded and say it is unseen; a failed save keeps it and says why.
 */
export function BeforeYouStart() {
  const settings = $api.useQuery("get", "/api/v1/settings/training");
  if (!settings.data || settings.data.before_you_start_seen) {
    return null;
  }
  return <Essentials />;
}

function Essentials() {
  const headingId = useId();
  const t = useT();
  const queryClient = useQueryClient();
  const [under18, setUnder18] = useState(false);
  const save = $api.useMutation("patch", "/api/v1/settings/training", {
    onSuccess: (updated) => {
      queryClient.setQueryData(trainingSettingsQueryKey, updated);
    },
  });
  const problems = apiErrorMessages(save.error);
  const close = () => {
    const body: Update = { before_you_start_seen: true };
    if (under18) body.no_max_efforts = true;
    save.mutate({ body });
  };

  return (
    <Panel
      tone="card"
      role="region"
      aria-labelledby={headingId}
      className="flex flex-col gap-2.5 px-4 py-3.5"
    >
      <h2 id={headingId} className="font-semibold text-sm">
        {t("onboarding.beforeYouStart.title")}
      </h2>
      <p className="text-ink-muted text-sm">
        {t("onboarding.beforeYouStart.lead")}
      </p>
      <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm">
        <li>{t("onboarding.beforeYouStart.pain")}</li>
        <li>{t("onboarding.beforeYouStart.redFlag")}</li>
        <li>{t("onboarding.beforeYouStart.easy")}</li>
        <li>{t("onboarding.beforeYouStart.notAdvice")}</li>
      </ul>
      <label className="flex min-h-[44px] items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-5"
          checked={under18}
          onChange={(event) => setUnder18(event.target.checked)}
        />
        {t("onboarding.beforeYouStart.under18")}
      </label>
      {under18 ? (
        <p className="text-sm">
          {t("onboarding.beforeYouStart.under18Message")}
        </p>
      ) : null}
      {problems.length > 0 ? (
        <p role="alert" className="text-destructive text-sm">
          {problems.join(" ")}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={save.isPending} onClick={close}>
          {t("onboarding.beforeYouStart.gotIt")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={save.isPending}
          onClick={close}
        >
          {t("onboarding.beforeYouStart.skip")}
        </Button>
      </div>
    </Panel>
  );
}
