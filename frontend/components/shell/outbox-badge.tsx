"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n/provider";
import { clearFailed } from "@/lib/offline/db";
import {
  badgeState,
  failedExport,
  type OutboxStatus,
  outboxStatus,
  startOutbox,
  subscribe,
} from "@/lib/offline/outbox";
import { syncNow } from "@/lib/offline/packet-store";

/** How often the badge re-reads the queue, so the 60 s rule can trip. */
const TICK_MS = 5_000;

/**
 * The outbox's one visible trace: a badge that appears only when something
 * needs the athlete — a queue waiting longer than a minute, an update
 * required, a login expired, or items the server refused — and the
 * "Couldn't upload" list behind it.
 *
 * Mounted once in the signed-in shell, which makes it the outbox's driver as
 * well: mounting starts `startOutbox` (a flush on launch, on visibility, on
 * `online`, every 30 s). So logging in again drains a queue a 401 kept — the
 * shell mounts and the launch flush runs. Each of those syncs also refreshes
 * the session packet after the upload (`syncNow`, `lib/offline/packet-store.ts`).
 *
 * The list is an inline panel under the badge (the filter/toggle tier): it is
 * a glance at a few items, with Copy and Export so nothing typed in the gym is
 * ever lost, and Clear only on the athlete's say-so.
 */
export function OutboxBadge() {
  const { t } = useI18n();
  const [status, setStatus] = useState<OutboxStatus | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const reread = useCallback(() => {
    outboxStatus()
      .then((next) => {
        setStatus(next);
        setNow(Date.now());
      })
      .catch(() => {
        // No IndexedDB (a private window): nothing can be queued either.
      });
  }, []);

  useEffect(() => {
    if (typeof indexedDB === "undefined") return;
    reread();
    const unsubscribe = subscribe(reread);
    const stop = startOutbox(syncNow);
    const tick = window.setInterval(reread, TICK_MS);
    return () => {
      unsubscribe();
      stop();
      window.clearInterval(tick);
    };
  }, [reread]);

  if (status === null) return null;
  const badge = badgeState(status, now);
  if (!badge.visible || badge.message === null) return null;

  const count =
    badge.message === "outbox.failed" ? status.failed.length : status.pending;
  const exported = failedExport(status.failed);

  return (
    <div className="px-6 pt-3">
      <button
        type="button"
        data-testid="outbox-badge"
        aria-expanded={status.failed.length > 0 ? open : undefined}
        onClick={() => setOpen((value) => !value)}
        className="rounded-md border border-warn-border bg-warn-surface px-2.5 py-1 text-ink text-xs"
      >
        {t(badge.message, { count })}
      </button>
      {open && status.failed.length > 0 ? (
        <section
          aria-label={t("outbox.failedTitle")}
          className="mt-2 flex max-w-lg flex-col gap-2 rounded-md border border-hairline bg-panel p-3"
        >
          <h2 className="font-semibold text-ink text-sm">
            {t("outbox.failedTitle")}
          </h2>
          <p className="text-ink-secondary text-xs">{t("outbox.failedHelp")}</p>
          <ul className="flex flex-col gap-1">
            {status.failed.map((item) => (
              <li
                key={item.id ?? item.mutation.mutation_id}
                className="text-ink-muted text-xs"
              >
                <span className="font-mono">{item.mutation.kind}</span> —{" "}
                {item.reason}
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                void navigator.clipboard
                  ?.writeText(exported)
                  .then(() => setCopied(true));
              }}
            >
              {copied ? t("outbox.copied") : t("outbox.copy")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              render={
                <a
                  href={`data:application/json;charset=utf-8,${encodeURIComponent(exported)}`}
                  download="arc-couldnt-upload.json"
                >
                  {t("outbox.export")}
                </a>
              }
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void clearFailed().then(reread);
                setOpen(false);
              }}
            >
              {t("outbox.clear")}
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
