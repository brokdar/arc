"use client";

import { useEffect, useState } from "react";

import { OfflineSessions } from "@/components/today/offline-sessions";
import { useI18n } from "@/lib/i18n/provider";

/** How long the week may take before the stored plan is shown instead. */
export const PENDING_BEFORE_PLAN_MS = 500;

/**
 * Today while the week is still being fetched.
 *
 * With no signal a request takes seconds to fail, and the athlete watched
 * "Loading today…" for about three and a half before the plan the phone
 * already holds appeared (strength UAT U3). So once the request has been out
 * for half a second — or at once when the browser already knows it is offline
 * — the stored packet's sessions are shown, under the loading line gone;
 * a fast answer never shows them, so an online visit does not flicker a
 * plan that is about to be replaced. With no stored packet it is the loading
 * line, as before.
 */
export function PendingToday({ today }: { readonly today: string }) {
  const { t } = useI18n();
  const [waited, setWaited] = useState(false);
  const [offline] = useState(
    () => typeof navigator !== "undefined" && navigator.onLine === false,
  );

  useEffect(() => {
    const timer = setTimeout(() => setWaited(true), PENDING_BEFORE_PLAN_MS);
    return () => clearTimeout(timer);
  }, []);

  const loading = (
    <p className="text-ink-muted text-sm">{t("today.loading")}</p>
  );
  if (!(waited || offline)) return loading;
  return <OfflineSessions today={today} fallback={loading} />;
}
