"use client";

import { useCallback, useRef, useState } from "react";

import { RedFlagControl } from "@/components/coach/red-flag";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import {
  AnchorForm,
  AnchorHistory,
  CurrentAnchors,
} from "@/components/settings/anchors";
import { ExportPanel } from "@/components/settings/export-panel";
import { IntegrationsPanel } from "@/components/settings/integrations/integrations-panel";
import { ProfileForm } from "@/components/settings/profile-form";
import { TrainingSettingsPanel } from "@/components/settings/training-settings";
import { ZonesPreview } from "@/components/settings/zones-preview";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import type { WritableAnchorType } from "@/lib/anchors";
import { useT } from "@/lib/i18n/provider";

/**
 * Settings: the numbers the rest of the application resolves against.
 *
 * Training leads (how arc behaves in the gym, which an athlete changes
 * between sessions on a phone), then the anchors, and everything else on the
 * page is arranged around them, because they are why this page exists — an athlete with no FTP has a
 * calendar full of percentages that resolve to nothing, and until now there
 * was no screen in the application that could enter one. The order is the
 * order of the question being answered: what is in force, how to change it,
 * what it produces, what it has been.
 *
 * The integrations come last: where rides come *from* is settled once and then
 * never looked at again, unlike an anchor, which is the number every screen
 * above resolves against.
 *
 * The profile and the illness flag come after, and are smaller on purpose.
 * The flag in particular is *not* re-implemented here — it is the same control
 * Today carries and the same dialog, mounted a second time (the banner is in the
 * shell so a raised flag is visible everywhere; this is where an athlete goes
 * looking for it when it is down).
 */
export function SettingsView() {
  const t = useT();
  const [anchorType, setAnchorType] = useState<WritableAnchorType>("ftp");
  const valueRef = useRef<HTMLInputElement>(null);

  /**
   * Point the append form at one anchor and put the cursor in it.
   *
   * What makes the empty states remedies rather than notices (UI convention
   * 3): "No FTP yet — add one to see power zones" is answered by a button
   * that leaves the athlete typing the value, not by one that scrolls them to
   * a form they still have to configure.
   */
  const startAppend = useCallback((next: WritableAnchorType) => {
    setAnchorType(next);
    // `scrollIntoView` is optional-called: jsdom does not implement it, and
    // the focus above is the part that matters.
    valueRef.current?.scrollIntoView?.({ block: "center" });
    valueRef.current?.focus();
  }, []);

  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">
          {t("settings.title")}
        </h1>
        <span className="text-ink-muted text-sm">{t("settings.subtitle")}</span>
      </Toolbar>

      <PageBody className="flex flex-col gap-5">
        {/* First, so it is one scroll away on a phone. It sat below the
            anchors, zones, history and profile, and the settings an athlete
            changes between sessions (units, mode, asking) were the ones
            hardest to reach (strength UAT U2). */}
        <TrainingSettingsPanel />

        <CurrentAnchors onAppend={startAppend} />

        <div className="flex flex-wrap items-start gap-[18px]">
          <AnchorForm
            className="min-w-0 flex-[1_1_480px]"
            anchorType={anchorType}
            onAnchorTypeChange={setAnchorType}
            valueRef={valueRef}
          />
          <ZonesPreview
            className="min-w-0 flex-[1_1_340px]"
            onAppend={startAppend}
          />
        </div>

        <AnchorHistory />

        <div className="flex flex-wrap items-start gap-[18px]">
          <ProfileForm className="min-w-0 flex-[1_1_480px]" />
          <HealthPanel className="min-w-0 flex-[1_1_340px]" />
        </div>

        {/* Last, and deliberately: this is the panel an athlete visits twice
            — once at setup and once when a ride stops arriving — while
            everything above it is read on the way past. */}
        <IntegrationsPanel />

        {/* After where rides come from, where everything goes: visited
            even more rarely, and never on the way to anything else. */}
        <ExportPanel />
      </PageBody>
    </>
  );
}

/**
 * The illness/injury flag, in the one place an athlete would look for a
 * setting.
 *
 * The control is imported rather than rebuilt. Its dialog already owns the
 * three fields and the rule that binds them — a flag that is up must carry a
 * severity — and a second form writing the same three columns would be a
 * second place for that rule to be wrong.
 */
function HealthPanel({ className }: { readonly className?: string }) {
  const t = useT();
  return (
    <Panel className={className}>
      <div className="flex flex-col items-start gap-2.5 px-5 py-4">
        <SectionLabel level={2}>{t("settings.health.title")}</SectionLabel>
        <p className="max-w-[62ch] text-ink-muted text-sm">
          {t("settings.health.body")}
        </p>
        <RedFlagControl />
      </div>
    </Panel>
  );
}
