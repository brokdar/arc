"use client";

import { InstallHint } from "@/components/shell/install-hint";
import { BeforeYouStart } from "@/components/strength/safety/before-you-start";
import { FirstRunQuestion } from "@/components/today/first-run";
import { $api } from "@/lib/api/client";

/**
 * The first-run panels, one at a time.
 *
 * A fresh install showed the install hint, *Before you start* and *Have you
 * lifted before?* together, which pushed the session's Start below the fold
 * on a phone (strength UAT U1). They now arrive in order — safety guidance
 * first, because it is the one a new lifter must not miss, then the question
 * that sets the mode, then the install hint — and each stays until it is
 * answered or skipped, which the settings record (`before_you_start_seen`,
 * `first_run_seen`). Displaced: showing them all with the install hint
 * collapsed — still three panels' worth of scrolling on the first visit.
 *
 * Nothing renders until the settings have loaded: a panel flashed at an
 * athlete who answered it last week is worse than one a visit late, and it
 * is what each panel already does on its own. Without settings (offline, the
 * request failing) the install hint, which needs none, is the only thing
 * that can show.
 */
export function FirstRunStack() {
  const settings = $api.useQuery("get", "/api/v1/settings/training");
  if (settings.isPending) return null;
  if (!settings.data) return <InstallHint />;
  if (!settings.data.before_you_start_seen) return <BeforeYouStart />;
  if (!settings.data.first_run_seen) return <FirstRunQuestion />;
  return <InstallHint />;
}
