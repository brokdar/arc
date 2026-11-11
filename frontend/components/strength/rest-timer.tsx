"use client";

/**
 * The rest timer, and the screen wake lock that keeps it in view.
 *
 * **Rest end alerts only while arc is open.** At zero the timer sounds,
 * flashes, vibrates where the phone can, and announces itself through a live
 * region — all of it from the open page. Nothing here asks for notification
 * permission or schedules anything for a locked screen: a notification needs
 * signal and a permission most athletes refuse, and a basement gym has
 * neither reliably. Displaced: promising locked-screen alerts, which would
 * have been a promise the gym floor breaks. Instead the screen is kept awake
 * (`WakeLockNotice`), and when the phone refuses, arc says so once.
 *
 * **Time.** The rest keeps a wall-clock end time (`endsAt`, epoch ms), so it
 * survives a reload and a phone that suspended the tab: reopened, the wall
 * clock is the only clock that kept counting. While the page is alive and
 * visible the end time is corrected by the monotonic clock — between two
 * ticks the wall clock should move as far as `performance.now()` did, and
 * when it does not (an NTP step, `.claude/rules/clock-monotonicity.md`) the
 * end time moves by the difference and is handed to `onChange` to store.
 * After the page was hidden the wall clock is trusted as it stands, because
 * a suspended page's monotonic clock stopped with it.
 *
 * **Sound** is a short tone through Web Audio, with the audio session set to
 * `ambient` where the browser has one, so it plays over the athlete's music
 * instead of stopping it. The context is unlocked by the tap that started
 * the rest (`primeRestSound`), since phones refuse audio no tap asked for.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n/provider";
import { cn } from "@/lib/utils";

export interface Clock {
  /** Wall-clock epoch ms — `Date.now`. */
  wall: () => number;
  /** Monotonic ms — `performance.now`. */
  mono: () => number;
}

export const SYSTEM_CLOCK: Clock = {
  wall: () => Date.now(),
  mono: () => performance.now(),
};

/** A wall/monotonic disagreement smaller than this is jitter, not a step. */
const STEP_TOLERANCE_MS = 1_000;
const TICK_MS = 250;
const FLASH_MS = 1_500;

type AudioCtor = new () => AudioContext;
let audio: AudioContext | null = null;

function audioContext(): AudioContext | null {
  if (audio) return audio;
  const w = window as unknown as {
    AudioContext?: AudioCtor;
    webkitAudioContext?: AudioCtor;
  };
  const Ctor = w.AudioContext ?? w.webkitAudioContext;
  if (!Ctor) return null;
  try {
    const session = (navigator as { audioSession?: { type: string } })
      .audioSession;
    if (session) session.type = "ambient";
    audio = new Ctor();
  } catch {
    audio = null;
  }
  return audio;
}

/** Unlock audio from inside a tap, so the rest end may sound later. */
export function primeRestSound(): void {
  try {
    void audioContext()?.resume();
  } catch {
    // No audio: the flash and the announcement still happen.
  }
}

/** The rest-end tone: short, high, mixed with whatever else is playing. */
export function playRestEnd(): void {
  try {
    const ctx = audioContext();
    if (!ctx) return;
    const tone = ctx.createOscillator();
    const gain = ctx.createGain();
    tone.frequency.value = 880;
    gain.gain.value = 0.25;
    tone.connect(gain);
    gain.connect(ctx.destination);
    tone.start();
    tone.stop(ctx.currentTime + 0.4);
  } catch {
    // Sound is a courtesy; the screen still flashes and announces.
  }
  try {
    navigator.vibrate?.([200, 100, 200]);
  } catch {
    // Not every phone vibrates for a web page.
  }
}

/** `m:ss` for a whole number of seconds. */
export function clockText(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Remaining rest in ms (negative once over), from a wall-clock end time
 * corrected by the monotonic clock while visible. See the module docstring.
 */
export function useRestRemaining(
  endsAt: number,
  onChange: (endsAt: number) => void,
  clock: Clock = SYSTEM_CLOCK,
): number {
  const end = useRef(endsAt);
  const last = useRef({ wall: clock.wall(), mono: clock.mono(), fresh: true });
  const [remaining, setRemaining] = useState(() => endsAt - clock.wall());
  const changed = useRef(onChange);
  changed.current = onChange;

  useEffect(() => {
    end.current = endsAt;
    setRemaining(endsAt - clock.wall());
  }, [endsAt, clock]);

  const tick = useCallback(() => {
    const wall = clock.wall();
    const mono = clock.mono();
    if (!last.current.fresh) {
      const drift = wall - last.current.wall - (mono - last.current.mono);
      if (Math.abs(drift) > STEP_TOLERANCE_MS) {
        end.current += drift;
        changed.current(end.current);
      }
    }
    last.current = { wall, mono, fresh: false };
    setRemaining(end.current - wall);
  }, [clock]);

  useEffect(() => {
    let timer: number | null = null;
    const start = () => {
      if (timer === null) timer = window.setInterval(tick, TICK_MS);
    };
    const stop = () => {
      if (timer !== null) window.clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        stop();
        // Whatever the clocks did while hidden, trust the wall clock.
        last.current = { ...last.current, fresh: true };
      } else {
        tick();
        start();
      }
    };
    last.current = { wall: clock.wall(), mono: clock.mono(), fresh: false };
    if (document.visibilityState !== "hidden") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [tick, clock]);

  return remaining;
}

export function RestTimer({
  endsAt,
  onChange,
  onSkip,
  clock = SYSTEM_CLOCK,
  className,
}: {
  endsAt: number;
  /** A new end time: a correction to store, or the athlete's ±30 s. */
  onChange: (endsAt: number) => void;
  onSkip: () => void;
  clock?: Clock;
  className?: string;
}) {
  const { t } = useI18n();
  const remaining = useRestRemaining(endsAt, onChange, clock);
  const [flash, setFlash] = useState(false);
  const [ended, setEnded] = useState(false);
  // An end is alerted only when it is seen happening: a rest already over
  // when the screen mounted (a reload long after) does not sound.
  const previous = useRef(remaining);

  useEffect(() => {
    const before = previous.current;
    previous.current = remaining;
    if (before > 0 && remaining <= 0) {
      playRestEnd();
      setEnded(true);
      setFlash(true);
      const off = window.setTimeout(() => setFlash(false), FLASH_MS);
      return () => window.clearTimeout(off);
    }
    if (remaining > 0) setEnded(false);
    return undefined;
  }, [remaining]);

  const over = remaining <= 0;
  const text = over
    ? t("rest.over", { time: clockText(Math.floor(-remaining / 1000)) })
    : clockText(Math.ceil(remaining / 1000));

  return (
    <div
      data-testid="rest-timer"
      data-flash={flash ? "true" : "false"}
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-hairline bg-panel px-3 py-1.5 transition-colors",
        flash && "animate-pulse border-accent-border bg-accent-surface",
        className,
      )}
    >
      <div
        role="timer"
        aria-label={t("rest.label")}
        className={cn(
          "font-mono font-semibold text-2xl tabular-nums",
          over ? "text-accent" : "text-ink",
        )}
      >
        {text}
      </div>
      <div className="ml-auto flex shrink-0 gap-1.5">
        <Button
          variant="outline"
          className="h-[44px] min-w-[44px] px-2 font-mono"
          aria-label={t("rest.minusLabel")}
          onClick={() => onChange(endsAt - 30_000)}
        >
          {t("rest.minus")}
        </Button>
        <Button
          variant="outline"
          className="h-[44px] min-w-[44px] px-2"
          aria-label={t("rest.skipLabel")}
          onClick={onSkip}
        >
          {t("rest.skip")}
        </Button>
        <Button
          variant="outline"
          className="h-[44px] min-w-[44px] px-2 font-mono"
          aria-label={t("rest.plusLabel")}
          onClick={() => onChange(endsAt + 30_000)}
        >
          {t("rest.plus")}
        </Button>
      </div>
      <p role="alert" className="sr-only">
        {ended ? t("rest.ended") : ""}
      </p>
    </div>
  );
}

/** Set once the wake-lock message has been shown on this install. */
export const WAKE_LOCK_NOTICE_KEY = "arc.wakeLockNotice";

function noticeShown(): boolean {
  try {
    return localStorage.getItem(WAKE_LOCK_NOTICE_KEY) === "shown";
  } catch {
    return false;
  }
}

function rememberNotice(): void {
  try {
    localStorage.setItem(WAKE_LOCK_NOTICE_KEY, "shown");
  } catch {
    // Without storage it may say it again next session; that is all.
  }
}

interface Sentinel {
  release: () => Promise<void>;
}

/**
 * Keep the screen awake for as long as this is mounted (the logging screen),
 * re-asking whenever the page becomes visible again — the browser drops the
 * lock on every hide. When the phone refuses or cannot, say once per install
 * that the phone may lock; nothing else changes (S9.13).
 */
export function WakeLockNotice() {
  const { t } = useI18n();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let live = true;
    let sentinel: Sentinel | null = null;
    const refused = () => {
      if (!live || noticeShown()) return;
      rememberNotice();
      setVisible(true);
    };
    const request = async () => {
      const lock = (
        navigator as {
          wakeLock?: { request: (type: "screen") => Promise<Sentinel> };
        }
      ).wakeLock;
      if (!lock) {
        refused();
        return;
      }
      try {
        const got = await lock.request("screen");
        if (live) sentinel = got;
        else void got.release().catch(() => undefined);
      } catch {
        refused();
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") void request();
    };
    void request();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      live = false;
      document.removeEventListener("visibilitychange", onVisibility);
      void sentinel?.release().catch(() => undefined);
    };
  }, []);

  if (!visible) return null;
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-lg border border-warn-border bg-warn-surface px-3 py-2 text-ink text-sm"
    >
      <p className="flex-1">{t("rest.wakeLock")}</p>
      <Button
        size="sm"
        variant="outline"
        className="h-[44px] min-w-[44px]"
        onClick={() => setVisible(false)}
      >
        {t("rest.wakeLockDismiss")}
      </Button>
    </div>
  );
}
