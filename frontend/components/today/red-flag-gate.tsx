"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type MouseEvent, type ReactNode, useState } from "react";

import { Panel } from "@/components/design/panel";
import { RED_FLAG_TEXT } from "@/components/strength/safety/safety-model";
import { useLocalState } from "@/components/today/local-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { useT } from "@/lib/i18n/provider";
import { type ActiveRedFlag, activeRedFlag } from "@/lib/red-flag-state";

const athleteQueryKey = $api.queryOptions("get", "/api/v1/athlete").queryKey;

/**
 * The red flag that is standing, as Today knows it: the server's athlete
 * record, or — before an upload has landed, or with no server — the phone's
 * own record of a gym stop (`lib/red-flag-state.ts`). `null` while the phone's
 * record is still being read, so nothing flashes.
 */
function useActiveRedFlag(): ActiveRedFlag | null {
  const athlete = $api.useQuery("get", "/api/v1/athlete");
  const local = useLocalState();
  if (local === null) return null;
  return activeRedFlag({
    athlete: athlete.data,
    local: local.sessions,
    flagUploadPending: local.flagUploadPending,
    now: Date.now(),
  });
}

/** Lower the server's flag. The one write both the panel and the gate make. */
function useClearFlag() {
  const queryClient = useQueryClient();
  return $api.useMutation("patch", "/api/v1/athlete", {
    onSuccess: (athlete) => {
      queryClient.setQueryData(athleteQueryKey, athlete);
      queryClient.invalidateQueries({ queryKey: athleteQueryKey });
    },
  });
}

/** The sentence for the flag's group — the gym screen's own words. */
function textOf(flag: ActiveRedFlag) {
  return RED_FLAG_TEXT[flag.kind];
}

/**
 * Above Today's session: the red flag that stands, in the words the gym
 * screen showed when it was raised.
 *
 * The shell's banner (`components/coach/red-flag.tsx`) shows only once the
 * server holds the flag, and carries the coach's wording; after a stop for
 * chest pain the athlete could open Today minutes later and see neither
 * (strength UAT B10). This one is shown for an emergency or severe flag, and
 * for any stop still waiting to upload, and says what to do — then offers
 * "All better" when the server holds a flag to lower.
 */
export function TodayRedFlag() {
  const t = useT();
  const flag = useActiveRedFlag();
  const clear = useClearFlag();
  if (flag === null || !(flag.severe || flag.source === "phone")) return null;
  const problems = apiErrorMessages(clear.error);

  return (
    <Panel
      tone="card"
      role="alert"
      data-testid="today-red-flag"
      className="flex flex-col gap-2 border-danger-border bg-danger-surface px-4 py-3.5"
    >
      <p className="font-semibold text-destructive text-lg">
        {t(textOf(flag))}
      </p>
      {flag.note ? (
        <p className="text-ink-secondary text-sm">{flag.note}</p>
      ) : null}
      {flag.source === "server" ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-ink-muted text-sm">
            {t("redFlagGate.panelNote")}
          </span>
          <Button
            size="sm"
            disabled={clear.isPending}
            onClick={() => clear.mutate({ body: { red_flag_active: false } })}
          >
            {clear.isPending ? t("illness.clearing") : t("illness.allBetter")}
          </Button>
        </div>
      ) : null}
      {problems.length > 0 ? (
        <p role="alert" className="text-destructive text-sm">
          {problems.join(" ")}
        </p>
      ) : null}
    </Panel>
  );
}

/**
 * Wraps the links that start training — Start, Log it now, Reopen, Train
 * without a plan — and puts a confirmation in front of them while an
 * emergency or severe red flag stands.
 *
 * Intercepts the click in the capture phase rather than replacing the links:
 * they stay the plain `<a>`s the service worker needs offline
 * (`components/strength/start-link.tsx`), and one wrapper covers every link
 * under it. The confirmation offers two ways out. *Not today* keeps the
 * athlete here. *I'm cleared, continue* is the athlete's own word that a
 * doctor cleared them or the symptom is gone — it lowers the server's flag
 * (the same write as "All better") and then follows the link; for a stop the
 * server has not heard of yet there is nothing to lower, so it follows the
 * link directly. A milder flag is not gated: it is shown, not enforced.
 */
export function RedFlagGate({ children }: { readonly children: ReactNode }) {
  const t = useT();
  const flag = useActiveRedFlag();
  const clear = useClearFlag();
  const [href, setHref] = useState<string | null>(null);
  const problems = apiErrorMessages(clear.error);

  const intercept = (event: MouseEvent) => {
    if (!flag?.severe) return;
    const link = (event.target as HTMLElement).closest("a");
    if (!link) return;
    event.preventDefault();
    event.stopPropagation();
    setHref(link.getAttribute("href"));
  };

  const proceed = () => {
    if (href !== null) window.location.assign(href);
  };

  const cleared = () => {
    if (flag?.source === "server") {
      clear.mutate(
        { body: { red_flag_active: false } },
        { onSuccess: proceed },
      );
    } else {
      proceed();
    }
  };

  return (
    <>
      <span className="contents" onClickCapture={intercept}>
        {children}
      </span>
      {href !== null && flag ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) {
              setHref(null);
              clear.reset();
            }
          }}
        >
          <DialogContent className="w-[min(26rem,calc(100vw-2rem))] gap-4 rounded-shell border border-hairline-card bg-panel p-5">
            <DialogHeader>
              <DialogTitle>{t("redFlagGate.title")}</DialogTitle>
              <DialogDescription className="font-semibold text-destructive text-base">
                {t(textOf(flag))}
              </DialogDescription>
            </DialogHeader>
            <p className="text-ink-secondary text-sm">
              {t("redFlagGate.body")}
            </p>
            {problems.length > 0 ? (
              <p role="alert" className="text-destructive text-sm">
                {problems.join(" ")}
              </p>
            ) : null}
            <div className="flex flex-col gap-2">
              <Button
                className="h-[52px] text-base"
                onClick={() => {
                  setHref(null);
                  clear.reset();
                }}
              >
                {t("redFlagGate.notToday")}
              </Button>
              <Button
                variant="outline"
                className="h-[44px]"
                disabled={clear.isPending}
                onClick={cleared}
              >
                {clear.isPending
                  ? t("redFlagGate.clearing")
                  : t("redFlagGate.cleared")}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}
