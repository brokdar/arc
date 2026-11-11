"use client";

import { useI18n } from "@/lib/i18n/provider";

/**
 * Start a strength session: a thumb-sized link to `/train/[id]`. Opening the
 * logging screen for a session not yet started starts it, so this one tap is
 * the whole of starting. Shown on Today online (beside the prescription,
 * which stays read-only) and offline (beside each session of the stored
 * packet).
 *
 * A plain `<a>`, not `next/link`: offline, a client-side navigation would
 * fetch the route's server payload and fail, while a document navigation is
 * answered by the service worker with the cached logging screen
 * (`public/sw.js`). Online it costs one page load at the start of a session.
 */
export function StartLink({ id, name }: { id: string; name: string }) {
  const { t } = useI18n();
  return (
    <a
      href={`/train/${id}`}
      aria-label={t("train.startLabel", { session: name })}
      className="inline-flex min-h-[56px] w-full items-center justify-center rounded-lg bg-primary px-6 font-semibold text-lg text-primary-foreground hover:bg-primary/80 sm:w-auto sm:self-start"
    >
      {t("train.start")}
    </a>
  );
}
