"use client";

import { UNPLANNED } from "@/components/strength/session-model";
import { useI18n } from "@/lib/i18n/provider";

/**
 * Today's two other ways into the gym screen, beside Start.
 *
 * - **Log it now** opens a planned session as a recap (`?recap=1`): one tap
 *   on "✓ all as planned" enters every prescribed set, `source: "recap"`.
 * - **Train without a plan** opens `/train/unplanned`, a session built from
 *   exercises added in the gym.
 *
 * Plain `<a>`s like `StartLink`, for the same reason: offline, the service
 * worker answers a document navigation with the cached logging screen.
 */

export function RecapLink({ id, name }: { id: string; name: string }) {
  const { t } = useI18n();
  return (
    <a
      href={`/train/${id}?recap=1`}
      aria-label={t("edits.recapLinkLabel", { session: name })}
      className="inline-flex min-h-[44px] items-center justify-center rounded-lg border border-hairline-strong px-4 text-ink sm:self-start"
    >
      {t("edits.recapLink")}
    </a>
  );
}

export function UnplannedLink() {
  const { t } = useI18n();
  return (
    <a
      href={`/train/${UNPLANNED}`}
      className="inline-flex min-h-[44px] items-center justify-center rounded-lg border border-hairline-strong px-3 text-ink text-sm"
    >
      {t("edits.unplanned")}
    </a>
  );
}
