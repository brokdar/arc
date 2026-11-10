"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import { Panel } from "@/components/design/panel";
import { Button } from "@/components/ui/button";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";

const CARDS_QUERY_PREFIX = ["get", "/api/v1/cards"] as const;

/**
 * The one suggestion card on Today: a headline, one "because" sentence, and
 * two answers.
 *
 * Which card, if any, is the backend's decision (`domain/cards.py`): at most
 * one, inside a daily and weekly budget, and **none while a session is
 * active** — so between Start and Finish `GET /cards` is `[]` and this renders
 * nothing. Nothing at all, not an empty frame: a card slot with no card in it
 * is a dead slot on the page the athlete opens every day (UI convention 3).
 *
 * "Not now" is a snooze until tomorrow, not a dismissal. A failed answer
 * keeps the card and says why beside it, so the athlete can try again.
 */
export function TodayCard() {
  const cards = $api.useQuery("get", "/api/v1/cards");
  const card = cards.data?.[0];
  if (!card) {
    return null;
  }
  return (
    <CardPanel
      key={card.id}
      id={card.id}
      headline={card.headline}
      because={card.because}
    />
  );
}

function CardPanel({
  id,
  headline,
  because,
}: {
  readonly id: string;
  readonly headline: string;
  readonly because: string;
}) {
  const headingId = useId();
  const queryClient = useQueryClient();
  const answer = $api.useMutation("post", "/api/v1/cards/{card_id}/act", {
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: CARDS_QUERY_PREFIX }),
  });
  const problems = apiErrorMessages(answer.error);

  const act = (action: "accept" | "snooze") =>
    answer.mutate({ params: { path: { card_id: id } }, body: { action } });

  return (
    <Panel
      tone="card"
      role="region"
      aria-labelledby={headingId}
      className="flex flex-col gap-2.5 px-4 py-3.5"
    >
      <h2 id={headingId} className="font-semibold text-sm">
        {headline}
      </h2>
      <p className="text-ink-muted text-sm">{because}</p>
      {problems.length > 0 ? (
        <p role="alert" className="text-destructive text-sm">
          {problems.join(" ")}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={answer.isPending}
          onClick={() => act("accept")}
        >
          Accept
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={answer.isPending}
          onClick={() => act("snooze")}
        >
          Not now
        </Button>
      </div>
    </Panel>
  );
}
