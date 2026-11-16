"use client";

import { type ReactNode, useEffect, useState } from "react";

import { Panel } from "@/components/design/panel";
import { PurposeBadge } from "@/components/design/purpose-badge";
import { SectionLabel } from "@/components/design/section-label";
import {
  RecapLink,
  UnplannedLink,
} from "@/components/strength/session-edits/today-links";
import { StartLink } from "@/components/strength/start-link";
import type { components } from "@/generated/api/schema";
import { formatDayMonthYear } from "@/lib/format";
import { useI18n } from "@/lib/i18n/provider";
import type { StoredPacket } from "@/lib/offline/db";
import {
  isStale,
  sessionsForToday,
  storedPacket,
} from "@/lib/offline/packet-store";
import { purposeLabel } from "@/lib/purpose";
import { describeSets } from "@/lib/workout-profile";

type PacketLine = components["schemas"]["PacketLineRead"];

/**
 * Today with no server: the sessions from the packet the last sync stored.
 *
 * Shown in place of "Could not load today" when the week could not be
 * fetched because no server answered. Everything on it is the packet's own
 * numbers — the pre-fill the server prepared, never one computed here. A
 * packet older than seven days still lists what it holds, under "Connect to
 * refresh": an old plan is still something to train from, and hiding it
 * would leave a basement gym with nothing.
 *
 * Renders `fallback` when the phone has never stored a packet (or has no
 * IndexedDB), so the page says what it said before packets existed.
 */
export function OfflineSessions({
  today,
  fallback,
  now = Date.now,
}: {
  today: string;
  fallback: ReactNode;
  now?: () => number;
}) {
  const { t } = useI18n();
  const [stored, setStored] = useState<StoredPacket | null | undefined>(
    undefined,
  );

  useEffect(() => {
    let live = true;
    storedPacket()
      .then((value) => {
        if (live) setStored(value);
      })
      .catch(() => {
        if (live) setStored(null);
      });
    return () => {
      live = false;
    };
  }, []);

  if (stored === undefined) return null;
  if (stored === null) return <>{fallback}</>;

  const { packet } = stored;
  const sessions = sessionsForToday(packet, today);
  const stale = isStale(packet, now());

  return (
    <section
      aria-label={t("offline.packetTitle")}
      className="flex flex-col gap-3"
      data-testid="offline-sessions"
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <SectionLabel level={2}>{t("offline.packetTitle")}</SectionLabel>
        <span className="font-mono text-ink-faint text-xs">
          {t("offline.packetSynced", {
            date: formatDayMonthYear(packet.generated_at.slice(0, 10)),
          })}
        </span>
        <span className="ml-auto">
          <UnplannedLink />
        </span>
        {stale ? (
          <span
            role="status"
            className="rounded-md border border-warn-border bg-warn-surface px-2 py-0.5 text-ink text-xs"
          >
            {t("offline.packetStale")}
          </span>
        ) : null}
      </div>
      {sessions.map((session) => (
        <Panel
          key={session.planned_session_id}
          tone="card"
          className="flex flex-col gap-2 rounded-shell px-[22px] py-4"
        >
          <header className="flex flex-wrap items-center gap-2.5">
            <PurposeBadge purpose={session.purpose} size="md" />
            <span className="font-mono text-ink-faint text-xs">
              {formatDayMonthYear(session.date)}
            </span>
          </header>
          {session.intent_text ? (
            <p className="text-ink-muted text-sm">{session.intent_text}</p>
          ) : null}
          <ul className="flex flex-col gap-1">
            {session.lines.map((line) => (
              <li
                key={line.line_ref}
                className="flex items-baseline justify-between gap-3 text-sm"
              >
                <span className="text-ink-secondary">{line.exercise_name}</span>
                <span className="font-mono text-ink text-xs">
                  {describeSets(line.prescription)} · {weightOf(line, t)}
                </span>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <StartLink
              id={session.planned_session_id}
              name={purposeLabel(session.purpose)}
            />
            <RecapLink
              id={session.planned_session_id}
              name={purposeLabel(session.purpose)}
            />
          </div>
        </Panel>
      ))}
    </section>
  );
}

function weightOf(
  line: PacketLine,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (line.prefill.starting_flow) return t("offline.noWeight");
  const kg = line.prefill.load_kg;
  return kg === null ? t("offline.bodyweight") : `${kg} kg`;
}
