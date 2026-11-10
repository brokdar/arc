"use client";

import { useEffect, useState } from "react";

import { useAthleteTimezone } from "@/lib/clock";
import { lastContact } from "@/lib/last-contact";

/**
 * "Ready offline · synced HH:MM" — whether this phone can open arc with no
 * network, and when it last heard from the server.
 *
 * Ready is `navigator.serviceWorker.ready`: the worker resolves it only after
 * its install precached Today and the offline page (`public/sw.js`), so the
 * line never promises an offline open the cache cannot keep. Synced is the
 * last signed-in answer from the server (`lib/last-contact.ts`), on the
 * athlete's clock like every other time in the app. Where no worker can run,
 * the line is absent rather than a "not ready" nobody can act on.
 */
export function OfflineReady() {
  const timezone = useAthleteTimezone();
  const [ready, setReady] = useState(false);
  const [synced, setSynced] = useState<Date | null>(null);

  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.serviceWorker) return;
    let live = true;
    void navigator.serviceWorker.ready.then(() => {
      if (!live) return;
      setSynced(lastContact());
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
      Ready offline
      {time ? (
        <>
          {" · synced "}
          <span className="font-mono">{time}</span>
        </>
      ) : null}
    </p>
  );
}
