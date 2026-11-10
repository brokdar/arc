"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import { Panel } from "@/components/design/panel";
import { Button } from "@/components/ui/button";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import type { MessageKey } from "@/lib/i18n/catalogue";
import { useT } from "@/lib/i18n/provider";
import {
  type LiftedBefore,
  trainingSettingsQueryKey,
} from "@/lib/training-settings";

type Update = components["schemas"]["TrainingSettingsUpdate"];

/** The answers, in the order the question offers them. */
const ANSWERS: Readonly<Record<LiftedBefore, MessageKey>> = {
  never: "onboarding.firstRun.never",
  a_little: "onboarding.firstRun.aLittle",
  regularly: "onboarding.firstRun.regularly",
};

/**
 * The first-run question: "Have you lifted before?", asked once, on Today.
 *
 * It asks about the athlete, not about arc: the answer sets the mode on the
 * server (`mode_for_answer` in `app/domain/training_mode.py` — *Never* or
 * skipping is Simple, any lifting is Standard), so nobody has to choose
 * between modes they have never seen. Skip is a real answer to "ask me
 * again?" and is stored as `first_run_seen`, leaving `lifted_before` empty.
 *
 * Shown only once the settings have loaded and say the question is unseen:
 * while loading, or if the read fails, it renders nothing, because a question
 * flashed at an athlete who answered it last week is worse than a question
 * asked one visit late. A failed answer keeps the question and says why.
 */
export function FirstRunQuestion() {
  const settings = $api.useQuery("get", "/api/v1/settings/training");
  if (!settings.data || settings.data.first_run_seen) {
    return null;
  }
  return <Question />;
}

function Question() {
  const headingId = useId();
  const t = useT();
  const queryClient = useQueryClient();
  const save = $api.useMutation("patch", "/api/v1/settings/training", {
    onSuccess: (updated) => {
      queryClient.setQueryData(trainingSettingsQueryKey, updated);
    },
  });
  const problems = apiErrorMessages(save.error);
  const send = (body: Update) => save.mutate({ body });

  return (
    <Panel
      tone="card"
      role="region"
      aria-labelledby={headingId}
      className="flex flex-col gap-2.5 px-4 py-3.5"
    >
      <h2 id={headingId} className="font-semibold text-sm">
        {t("onboarding.firstRun.question")}
      </h2>
      <p className="text-ink-muted text-sm">{t("onboarding.firstRun.why")}</p>
      {problems.length > 0 ? (
        <p role="alert" className="text-destructive text-sm">
          {problems.join(" ")}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {(Object.keys(ANSWERS) as LiftedBefore[]).map((answer) => (
          <Button
            key={answer}
            size="sm"
            disabled={save.isPending}
            onClick={() => send({ lifted_before: answer })}
          >
            {t(ANSWERS[answer])}
          </Button>
        ))}
        <Button
          size="sm"
          variant="ghost"
          disabled={save.isPending}
          onClick={() => send({ first_run_seen: true })}
        >
          {t("onboarding.firstRun.skip")}
        </Button>
      </div>
    </Panel>
  );
}
