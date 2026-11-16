"use client";

import { useId } from "react";

import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { buttonVariants } from "@/components/ui/button";
import { STRONG_CSV_URL, setsCsvUrl, WORKOUTS_JSON_URL } from "@/lib/exports";
import { useT } from "@/lib/i18n/provider";

/**
 * Everything logged, out of arc: the answer to lock-in (direction §11.2).
 *
 * Plain download links — the API names each file — in the order of how much
 * they keep: the JSON holds every field arc stores, the set CSV one row per
 * set for a spreadsheet, and the Strong CSV only what Strong's columns can
 * say, which is what Hevy and most other apps import. The semicolon CSV is
 * for a spreadsheet set to a decimal comma (German Excel), where a comma file
 * opens as one column.
 */
export function ExportPanel({ className }: { readonly className?: string }) {
  const headingId = useId();
  const t = useT();
  const link = buttonVariants({ variant: "outline", size: "sm" });

  return (
    <Panel className={className} role="region" aria-labelledby={headingId}>
      <div className="flex flex-col gap-3 px-5 py-4">
        <SectionLabel level={2} id={headingId}>
          {t("settings.export.title")}
        </SectionLabel>
        <p className="max-w-[62ch] text-ink-muted text-sm">
          {t("settings.export.body")}
        </p>
        <div className="flex flex-wrap gap-2">
          <a className={link} href={WORKOUTS_JSON_URL} download>
            {t("settings.export.json")}
          </a>
          <a className={link} href={setsCsvUrl("comma")} download>
            {t("settings.export.csv")}
          </a>
          <a className={link} href={setsCsvUrl("semicolon")} download>
            {t("settings.export.csvSemicolon")}
          </a>
          <a className={link} href={STRONG_CSV_URL} download>
            {t("settings.export.strong")}
          </a>
        </div>
        <p className="max-w-[62ch] text-ink-muted text-xs">
          {t("settings.export.note")}
        </p>
      </div>
    </Panel>
  );
}
