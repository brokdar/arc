"use client";

import { useEffect, useState } from "react";

import { useAthleteTimezone } from "@/lib/clock";
import { useI18n } from "@/lib/i18n/provider";
import { storedPacket } from "@/lib/offline/packet-store";

/**
 * "Ready offline · plan from HH:MM" — whether this phone can open arc with no
 * network, and how old the plan it would open is.
 *
 * Ready is `navigator.serviceWorker.ready`: the worker resolves it only after
 * its install precached Today and the offline page (`public/sw.js`), so the
 * line never promises an offline open the cache cannot keep. The time is
 * when the stored session packet was fetched (`lib/offline/packet-store.ts`),
 * on the athlete's clock like every other time in the app. It used to be the
 * last answer from the server, which is newer than the packet whenever the
 * five-minute refresh throttle was holding it back: "synced 12:18" sat over a
 * plan from 12:16 and promised settings the phone did not have (strength UAT
 * B4). With no stored packet there is no time to give. Where no worker can run,
 * the line is absent rather than a "not ready" nobody can act on.
 */
export function OfflineReady() {
  const timezone = useAthleteTimezone();
  const { t, rich } = useI18n();
  const [ready, setReady] = useState(false);
  const [synced, setSynced] = useState<Date | null>(null);

  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.serviceWorker) return;
    let live = true;
    void navigator.serviceWorker.ready.then(async () => {
      const stored = await storedPacket().catch(() => null);
      if (!live) return;
      setSynced(stored ? new Date(stored.storedAt) : null);
      setReady(true);
    });
    return () => {
      live = false;
    };
  }, []);

  if (!ready) return null;
  const time = synced
    ? new Intl.DateTimeFormat("en-GB", {
        timeZone: timezone,
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(synced)
    : null;

  return (
    <p className="text-ink-muted text-xs" data-testid="offline-ready">
      {time
        ? rich("offline.readyPlan", {
            time: <span className="font-mono">{time}</span>,
          })
        : t("offline.ready")}
    </p>
  );
}
