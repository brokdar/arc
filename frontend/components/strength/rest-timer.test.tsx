import "fake-indexeddb/auto";

import { act, render, screen } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import {
  RestTimer,
  WAKE_LOCK_NOTICE_KEY,
  WakeLockNotice,
} from "@/components/strength/rest-timer";
import type { ActiveSession } from "@/components/strength/session-model";
import { saveSession } from "@/components/strength/session-store";
import { putPacket } from "@/lib/offline/db";
import { athleteToday } from "@/tests/mocks/fixtures";
import { packetFixture } from "@/tests/mocks/handlers-packet";

/**
 * AC-51: the rest timer keeps a wall-clock end time and corrects it with the
 * monotonic clock while the page is alive, so it is right to the second
 * after 90 s in the background and after the system clock steps back 30 s.
 *
 * The two clocks are a test-owned pair (`wall` for `Date.now`, `mono` for
 * `performance.now`), advanced together for time that passes visibly and
 * separately for the cases the timer must survive: a phone that suspends a
 * hidden page (monotonic time stops), and an NTP step (wall time jumps).
 * The tick is vitest's fake `setInterval`, advanced in step with the clocks.
 */

const T0 = Date.UTC(2026, 10, 11, 18, 0);
let wall = T0;
let mono = 5_000;
const clock = { wall: () => wall, mono: () => mono };

/** Time passing while the page is visible: both clocks, tick by tick. */
function pass(ms: number) {
  for (let done = 0; done < ms; done += 250) {
    wall += 250;
    mono += 250;
    act(() => {
      vi.advanceTimersByTime(250);
    });
  }
}

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

/** A stand-in Web Audio context, recording every tone started. */
const tones: number[] = [];
class FakeAudioContext {
  currentTime = 0;
  state = "running";
  destination = {};
  resume() {
    return Promise.resolve();
  }
  createGain() {
    return { gain: { value: 1 }, connect: () => undefined };
  }
  createOscillator() {
    return {
      frequency: { value: 0 },
      connect: () => undefined,
      start: () => tones.push(Date.now()),
      stop: () => undefined,
    };
  }
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"],
  });
  wall = T0;
  mono = 5_000;
  tones.length = 0;
  vi.stubGlobal("AudioContext", FakeAudioContext);
  setVisibility("visible");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function renderTimer(seconds: number) {
  const onChange = vi.fn();
  const view = render(
    <RestTimer
      endsAt={T0 + seconds * 1000}
      onChange={onChange}
      onSkip={() => undefined}
      clock={clock}
    />,
  );
  return { onChange, ...view };
}

describe("remaining time", () => {
  it("counts down from the end time", () => {
    renderTimer(120);
    expect(screen.getByRole("timer")).toHaveTextContent("2:00");
    pass(10_000);
    expect(screen.getByRole("timer")).toHaveTextContent("1:50");
  });

  it("is right after 90 s hidden, while the browser kept time", () => {
    renderTimer(120);
    setVisibility("hidden");
    wall += 90_000;
    mono += 90_000;
    act(() => {
      vi.advanceTimersByTime(90_000);
    });
    setVisibility("visible");

    expect(screen.getByRole("timer")).toHaveTextContent("0:30");
  });

  it("is right after 90 s hidden on a phone that suspended the page", () => {
    // A suspended page's monotonic clock stops; only the wall clock knows.
    renderTimer(120);
    setVisibility("hidden");
    wall += 90_000;
    setVisibility("visible");

    expect(screen.getByRole("timer")).toHaveTextContent("0:30");
  });

  it("is right after the system clock steps back 30 s", () => {
    const { onChange } = renderTimer(120);
    pass(10_000);
    wall -= 30_000;
    pass(250);

    // 120 − 10.25 s visible: still 1:50 (ceil), not 2:20.
    expect(screen.getByRole("timer")).toHaveTextContent("1:50");
    // The stored end time moves with the clock, so a reload agrees.
    expect(onChange).toHaveBeenLastCalledWith(T0 + 120_000 - 30_000);
    pass(109_750);
    expect(screen.getByRole("timer")).toHaveTextContent("+0:00 over");
  });
});

describe("rest end", () => {
  it("sounds, flashes, announces and counts on past zero", () => {
    const vibrate = vi.fn(() => true);
    Object.defineProperty(navigator, "vibrate", {
      configurable: true,
      value: vibrate,
    });
    renderTimer(3);
    expect(tones).toHaveLength(0);
    expect(screen.getByRole("alert")).toHaveTextContent("");

    pass(3_000);
    expect(tones).toHaveLength(1);
    expect(vibrate).toHaveBeenCalled();
    expect(screen.getByTestId("rest-timer")).toHaveAttribute(
      "data-flash",
      "true",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Rest is over. Time for the next set.",
    );

    pass(5_000);
    expect(screen.getByRole("timer")).toHaveTextContent("+0:05 over");
    // One rest, one alert.
    expect(tones).toHaveLength(1);
  });

  it("does not sound for a rest that was already over when it mounted", () => {
    renderTimer(-10);
    pass(1_000);
    expect(tones).toHaveLength(0);
    expect(screen.getByRole("timer")).toHaveTextContent("+0:11 over");
  });
});

describe("a reload mid-rest", () => {
  beforeEach(async () => {
    await writesSettled();
    globalThis.indexedDB = new IDBFactory();
  });

  it("restores the timer from the stored end time", async () => {
    const today = athleteToday();
    const packet = packetFixture(today, new Date(T0));
    await putPacket({ packet, storedAt: T0 });
    const session = packet.sessions[0];
    const line = session.lines[0];
    const stored: ActiveSession = {
      plannedSessionId: session.planned_session_id,
      sessionClientId: "0199c000-0000-7000-8000-00000000c001",
      startedAt: T0 - 120_000,
      lineIndex: 0,
      finished: false,
      rest: { endsAt: T0 + 75_000 },
      sets: [
        {
          clientId: "0199c000-0000-7000-8000-00000000d001",
          lineRef: line.line_ref,
          exerciseId: line.exercise_id,
          warmup: false,
          loadKg: 20,
          reps: 10,
          durationS: null,
          perSide: true,
          rir: null,
          rirCapped: false,
          effortSimple: null,
        },
      ],
    };
    await saveSession(stored);

    vi.useRealTimers();
    render(
      <LoggingScreen
        plannedSessionId={session.planned_session_id}
        clock={clock}
      />,
    );
    const resume = await screen.findByRole("button", {
      name: "Resume — set 2 of Single-Arm Dumbbell Row",
    });
    act(() => resume.click());

    expect(await screen.findByRole("timer")).toHaveTextContent("1:15");
  });
});

describe("wake lock", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  function refuseWakeLock() {
    Object.defineProperty(navigator, "wakeLock", {
      configurable: true,
      value: {
        request: vi.fn(() => Promise.reject(new Error("NotAllowedError"))),
      },
    });
  }

  it("says once per install that the phone may lock, when refused", async () => {
    vi.useRealTimers();
    refuseWakeLock();
    const first = render(<WakeLockNotice />);
    expect(
      await screen.findByText(
        "Your phone may lock between sets. Rest alerts then need notifications.",
      ),
    ).toBeVisible();
    expect(localStorage.getItem(WAKE_LOCK_NOTICE_KEY)).toBe("shown");
    first.unmount();

    render(<WakeLockNotice />);
    // Let the refused request settle before asserting it said nothing.
    await act(async () => {
      await Promise.resolve();
    });
    expect(
      screen.queryByText(/Your phone may lock between sets/),
    ).not.toBeInTheDocument();
  });

  it("says nothing when the screen is kept awake", async () => {
    vi.useRealTimers();
    const release = vi.fn(() => Promise.resolve());
    const request = vi.fn(() =>
      Promise.resolve({ release, addEventListener: () => undefined }),
    );
    Object.defineProperty(navigator, "wakeLock", {
      configurable: true,
      value: { request },
    });
    const view = render(<WakeLockNotice />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(request).toHaveBeenCalledWith("screen");
    expect(
      screen.queryByText(/Your phone may lock between sets/),
    ).not.toBeInTheDocument();
    view.unmount();
    expect(release).toHaveBeenCalled();
  });
});
