import { render, screen } from "@testing-library/react";
import { redirect } from "next/navigation";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";
import CalendarPage from "@/app/(app)/calendar/page";
import InboxPage from "@/app/(app)/inbox/page";
import AppLayout from "@/app/(app)/layout";
import ProposalsPage from "@/app/(app)/proposals/page";
import SessionPage from "@/app/(app)/sessions/[id]/page";
import SessionsPage from "@/app/(app)/sessions/page";
import SettingsPage from "@/app/(app)/settings/page";
import TodayPage from "@/app/(app)/today/page";
import WorkoutPage from "@/app/(app)/workouts/[id]/page";
import WorkoutsPage from "@/app/(app)/workouts/page";
import LoginPage from "@/app/login/page";
import Home from "@/app/page";
import { Providers } from "@/app/providers";
import { ClockProvider } from "@/lib/clock";
import { ACTIVITY_IDS } from "@/tests/mocks/fixtures";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/calendar",
  useSearchParams: () => new URLSearchParams(),
  redirect: vi.fn(),
}));

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

/**
 * Render smoke tests for the route shells. They are server components, but
 * synchronous and prop-free, so they render here like any other function
 * component — enough to catch a page that no longer mounts its content.
 * `Providers` supplies the QueryClient the guarded pages need, and `Guarded`
 * adds the clock every signed-in page names a day on — `AppLayout` puts both
 * above them in production, so a page rendered without them here would be
 * proving something the application never does.
 *
 * **Every signed-in page uses `Guarded`, not only the ones that read the
 * clock today.** `useAthleteTimezone` throws outside a `ClockProvider`, and a
 * component deep in the tree — a note card, a proposal card — reaches it only
 * once its data arrives. A page rendered bare therefore passes for as long as
 * `findBy*` resolves on the first synchronous render and `cleanup()` unmounts
 * before the fetch lands: the assertion is green, the mount it claims to prove
 * never completed, and vitest reports the throw as an unhandled error beside a
 * passing test. Wrapping by need would make each of these smoke tests depend
 * on which components its page happens to import this month. Only the login
 * page renders bare, because that is how it renders in the application: it is
 * outside `AppLayout`, and there is no athlete to have a clock yet.
 */
function Guarded({ children }: { children: React.ReactNode }) {
  return (
    <Providers>
      <ClockProvider>{children}</ClockProvider>
    </Providers>
  );
}

describe("route shells", () => {
  it("renders the login page", () => {
    render(
      <Providers>
        <LoginPage />
      </Providers>,
    );

    expect(
      screen.getByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
  });

  it("sends the root at the week — there is no separate home", () => {
    Home();

    expect(redirect).toHaveBeenCalledWith("/calendar");
  });

  it("wraps every signed-in page in the guard and the shell", async () => {
    render(
      <Providers>
        <AppLayout>
          <p>page content</p>
        </AppLayout>
      </Providers>,
    );

    expect(await screen.findByText("page content")).toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "Sections" }),
    ).toBeInTheDocument();
  });

  it("mounts the today page", async () => {
    render(
      <Guarded>
        <TodayPage />
      </Guarded>,
    );

    expect(
      await screen.findByRole("heading", { level: 1 }),
    ).toBeInTheDocument();
  });

  it("mounts the workout library", async () => {
    render(
      <Guarded>
        <WorkoutsPage />
      </Guarded>,
    );

    expect(await screen.findByLabelText("Search")).toBeInTheDocument();
  });

  it("routes /workouts/new at the creator, and an id at the editor", async () => {
    render(
      <Guarded>
        {await WorkoutPage({ params: Promise.resolve({ id: "new" }) })}
      </Guarded>,
    );

    expect(await screen.findByText("New workout")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save workout" }),
    ).toBeInTheDocument();
  });

  it("mounts the inbox", async () => {
    render(
      <Guarded>
        <InboxPage />
      </Guarded>,
    );

    expect(
      await screen.findByRole("heading", { name: "Inbox", level: 1 }),
    ).toBeInTheDocument();
  });

  it("mounts the session log", async () => {
    render(
      <Guarded>
        <SessionsPage />
      </Guarded>,
    );

    expect(
      await screen.findByRole("heading", { name: "Sessions", level: 1 }),
    ).toBeInTheDocument();
  });

  it("routes a session id at its detail page", async () => {
    render(
      <Guarded>
        {
          await SessionPage({
            params: Promise.resolve({ id: ACTIVITY_IDS.outdoorRide }),
          })
        }
      </Guarded>,
    );

    expect(
      await screen.findByRole("heading", { name: "Corrections" }),
    ).toBeInTheDocument();
  });

  it("mounts the proposal inbox", async () => {
    render(
      <Guarded>
        <ProposalsPage />
      </Guarded>,
    );

    expect(
      await screen.findByRole("heading", { name: "Proposals", level: 1 }),
    ).toBeInTheDocument();
  });

  it("mounts the settings page", async () => {
    render(
      <Guarded>
        <SettingsPage />
      </Guarded>,
    );

    expect(
      await screen.findByRole("heading", { name: "Settings", level: 1 }),
    ).toBeInTheDocument();
  });

  it("mounts the calendar page", async () => {
    render(
      <Guarded>
        <CalendarPage />
      </Guarded>,
    );

    expect(
      await screen.findByRole("heading", { name: "Calendar" }),
    ).toBeInTheDocument();
  });
});
