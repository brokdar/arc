"use client";

import Link from "next/link";
import { mayReopen } from "@/components/strength/session-edits/lifecycle";
import { RecapLink } from "@/components/strength/session-edits/today-links";
import { StartLink } from "@/components/strength/start-link";
import { useLocalState } from "@/components/today/local-state";
import { RedFlagGate } from "@/components/today/red-flag-gate";
import { useI18n } from "@/lib/i18n/provider";

/**
 * What a strength session on Today offers, by where it stands.
 *
 * - **Still to do** — Start and Log it now, behind the red flag gate.
 * - **Under way on this phone** — Resume, straight back into the logging
 *   screen. Not gated: it is a session the athlete already began, and Start
 *   alone told them nothing about it (strength UAT U5).
 * - **Done** — a done line, a link to the recorded session once the server
 *   has matched one, and Reopen inside the 24-hour window the gym screen
 *   keeps (`mayReopen`) — until now reachable only by typing `/train/<id>`.
 *
 * "Under way" and "done on this phone" come from the stored session
 * (`useLocalState`); "done" also comes from the server's own status, for a
 * session trained on another device. Renders nothing until the phone's record
 * has been read, so Start never flashes over a session that is under way.
 */
export function SessionActions({
  id,
  name,
  status,
  matchedSessionId,
}: {
  readonly id: string;
  readonly name: string;
  /** The server's status; `undefined` offline, where only the phone knows. */
  readonly status?: string;
  readonly matchedSessionId?: string | null;
}) {
  const { t } = useI18n();
  const local = useLocalState();
  if (local === null) return null;

  const record = local.sessions.find(
    (session) => session.plannedSessionId === id,
  );
  const finished = record?.finished === true || status === "completed";
  if (status !== undefined && status !== "planned" && !finished) return null;

  if (record && !record.finished) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-ink-faint text-xs">
          {t("today.inProgress")}
        </span>
        <a
          href={`/train/${id}`}
          aria-label={t("today.resumeLabel", { session: name })}
          className="inline-flex min-h-[56px] w-full items-center justify-center rounded-lg bg-primary px-6 font-semibold text-lg text-primary-foreground hover:bg-primary/80 sm:w-auto sm:self-start"
        >
          {t("today.resume")}
        </a>
      </div>
    );
  }

  if (finished) {
    const reopen = record !== undefined && mayReopen(record, Date.now());
    const recorded = status === "completed" || Boolean(matchedSessionId);
    return (
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium text-status-completed text-sm">
          {recorded ? t("today.done") : t("today.doneUploading")}
        </span>
        {matchedSessionId ? (
          <Link
            href={`/sessions/${matchedSessionId}`}
            aria-label={t("today.openRecordedLabel", { session: name })}
            className="text-accent text-sm hover:text-accent-hover"
          >
            {t("today.openRecorded")}
          </Link>
        ) : null}
        {reopen ? (
          <RedFlagGate>
            <a
              href={`/train/${id}`}
              aria-label={t("today.reopenLabel", { session: name })}
              className="inline-flex min-h-[44px] items-center justify-center rounded-lg border border-hairline-strong px-4 text-ink"
            >
              {t("today.reopen")}
            </a>
          </RedFlagGate>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      <RedFlagGate>
        <StartLink id={id} name={name} />
        <RecapLink id={id} name={name} />
      </RedFlagGate>
    </div>
  );
}
