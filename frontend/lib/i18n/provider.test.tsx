import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TrainingSettingsPanel } from "@/components/settings/training-settings";
import { TodayView } from "@/components/today/today-view";
import { AthleteClock } from "@/lib/clock";
import { I18nProvider, LocaleProvider, useI18n } from "@/lib/i18n/provider";
import { ATHLETE_TIMEZONE } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";
import { patchTrainingSettings } from "@/tests/mocks/training-settings-state";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: React.PropsWithChildren<{ href: string }>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

const LOCALE_KEY = "arc:locale";

/** A weight as the gym screen will print one, through the provider. */
function Weight({ kg }: { kg: number }) {
  const { weight } = useI18n();
  return <output data-testid="weight">{weight(kg)}</output>;
}

/** A weekday and a placeholder number, as the provider renders them. */
function Probe() {
  const { weekday, t } = useI18n();
  return (
    <p data-testid="probe">
      {weekday("2026-07-27")} · {t("units.setsOther", { count: 1250 })}
    </p>
  );
}

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

/** The signed-in tree as `app/(app)/layout.tsx` builds it, plus Settings. */
function renderApp() {
  return render(
    <QueryClientProvider client={client()}>
      <AthleteClock timezone={ATHLETE_TIMEZONE}>
        <I18nProvider>
          <Weight kg={102.5} />
          <TodayView />
          <TrainingSettingsPanel />
        </I18nProvider>
      </AthleteClock>
    </QueryClientProvider>,
  );
}

function todayHeading() {
  return screen.getByRole("heading", { level: 1 });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

describe("I18nProvider (AC-20)", () => {
  it("with locale de renders 102,5 kg and the German Today heading", async () => {
    patchTrainingSettings({ locale: "de" });
    renderApp();

    expect(await screen.findByText("102,5 kg")).toBeInTheDocument();
    expect(todayHeading()).toHaveTextContent("Heute");
    expect(document.documentElement.lang).toBe("de");
  });

  it("switching the setting to en re-renders 102.5 kg without a reload", async () => {
    const user = userEvent.setup();
    patchTrainingSettings({ locale: "de" });
    renderApp();
    await screen.findByText("102,5 kg");

    // The Settings panel's own language control: the real PATCH, answered by
    // the stateful handler, is what the provider has to follow.
    await user.selectOptions(await screen.findByLabelText("Language"), "en");

    expect(await screen.findByText("102.5 kg")).toBeInTheDocument();
    expect(screen.queryByText("102,5 kg")).not.toBeInTheDocument();
    expect(todayHeading()).toHaveTextContent("Today");
    expect(document.documentElement.lang).toBe("en");
  });

  it("offline, it keeps the language the server last served on this phone", async () => {
    patchTrainingSettings({ locale: "de" });
    const first = renderApp();
    await screen.findByText("102,5 kg");
    first.unmount();

    server.use(
      http.untyped.get("http://localhost:8000/api/v1/settings/training", () =>
        Response.error(),
      ),
    );
    renderApp();
    expect(screen.getByText("102,5 kg")).toBeInTheDocument();
  });

  it("before the settings arrive, with nothing remembered, it speaks English", () => {
    patchTrainingSettings({ locale: "de" });
    renderApp();
    expect(screen.getByTestId("weight")).toHaveTextContent("102.5 kg");
  });
});

describe("unknown locale", () => {
  it("falls back to English", () => {
    render(
      <QueryClientProvider client={client()}>
        <AthleteClock timezone={ATHLETE_TIMEZONE}>
          <LocaleProvider locale="fr">
            <Weight kg={102.5} />
            <TodayView />
          </LocaleProvider>
        </AthleteClock>
      </QueryClientProvider>,
    );
    expect(screen.getByText("102.5 kg")).toBeInTheDocument();
    expect(todayHeading()).toHaveTextContent("Today");
  });

  it("a remembered value that is not a locale falls back to English", () => {
    window.localStorage.setItem(LOCALE_KEY, "fr");
    server.use(
      http.untyped.get("http://localhost:8000/api/v1/settings/training", () =>
        Response.error(),
      ),
    );
    renderApp();
    expect(screen.getByText("102.5 kg")).toBeInTheDocument();
    expect(todayHeading()).toHaveTextContent("Today");
  });
});

describe("dates in the athlete's language", () => {
  it("names weekdays and writes numbers in placeholders the German way", () => {
    render(
      <LocaleProvider locale="de">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("Mo · 1.250 Sätze");
  });

  it("and the English way", () => {
    render(
      <LocaleProvider locale="en">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("Mon · 1,250 sets");
  });
});
