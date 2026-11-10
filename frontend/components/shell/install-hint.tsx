"use client";

import { useEffect, useState } from "react";

import { useI18n } from "@/lib/i18n/provider";

const DISMISSED_KEY = "arc:install-hint-dismissed";

/** iPhone/iPad Safari, not yet running from the home screen. */
function iosBrowserTab(): boolean {
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const standalone =
    (navigator as Navigator & { standalone?: boolean }).standalone === true ||
    window.matchMedia?.("(display-mode: standalone)").matches === true;
  return ios && !standalone;
}

function dismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) !== null;
  } catch {
    return false;
  }
}

/**
 * How to install arc on an iPhone, and the one step people miss.
 *
 * iOS offers no install prompt — Android's browser does, so the hint shows
 * only on iOS, and only in a browser tab. An app added to the home screen does
 * not share Safari's cookies, so an athlete who logged in in Safari opens the
 * installed app to the login page and thinks the sign-in was lost; the hint
 * says to log in inside the installed app. Dismissal is remembered on this
 * device only.
 */
export function InstallHint() {
  const { t, rich } = useI18n();
  const [show, setShow] = useState(false);

  useEffect(() => {
    setShow(iosBrowserTab() && !dismissed());
  }, []);

  if (!show) return null;

  function dismiss() {
    try {
      window.localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      // Storage blocked: hidden for this visit only.
    }
    setShow(false);
  }

  return (
    <aside
      role="note"
      aria-label={t("install.label")}
      className="flex items-start gap-3 rounded-card border border-hairline bg-card px-3.5 py-2.5 text-ink-secondary text-sm"
    >
      <p className="min-w-0 flex-1">
        {rich("install.steps", {
          share: <span className="text-ink">{t("install.share")}</span>,
          addToHome: <span className="text-ink">{t("install.addToHome")}</span>,
        })}
      </p>
      <button
        type="button"
        onClick={dismiss}
        aria-label={t("install.dismiss")}
        className="min-h-11 min-w-11 shrink-0 text-ink-muted hover:text-ink"
      >
        ×
      </button>
    </aside>
  );
}
