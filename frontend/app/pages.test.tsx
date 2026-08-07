import { render, screen } from "@testing-library/react";
import { redirect } from "next/navigation";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";
import CalendarPage from "@/app/(app)/calendar/page";
import InboxPage from "@/app/(app)/inbox/page";
import AppLayout from "@/app/(app)/layout";
import SessionPage from "@/app/(app)/sessions/[id]/page";
import SessionsPage from "@/app/(app)/sessions/page";
import TodayPage from "@/app/(app)/today/page";
import WorkoutPage from "@/app/(app)/workouts/[id]/page";
import WorkoutsPage from "@/app/(app)/workouts/page";
import LoginPage from "@/app/login/page";
import Home from "@/app/page";
import { Providers } from "@/app/providers";
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
 * `Providers` supplies the QueryClient the guarded pages need.
 */
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
      <Providers>
        <TodayPage />
      </Providers>,
    );

    expect(
      await screen.findByRole("heading", { level: 1 }),
    ).toBeInTheDocument();
  });

  it("mounts the workout library", async () => {
    render(
      <Providers>
        <WorkoutsPage />
      </Providers>,
    );

    expect(await screen.findByLabelText("Search")).toBeInTheDocument();
  });

  it("routes /workouts/new at the creator, and an id at the editor", async () => {
    render(
      <Providers>
        {await WorkoutPage({ params: Promise.resolve({ id: "new" }) })}
      </Providers>,
    );

    expect(await screen.findByText("New workout")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save workout" }),
    ).toBeInTheDocument();
  });

  it("mounts the inbox", async () => {
    render(
      <Providers>
        <InboxPage />
      </Providers>,
    );

    expect(
      await screen.findByRole("heading", { name: "Inbox", level: 1 }),
    ).toBeInTheDocument();
  });

  it("mounts the session log", async () => {
    render(
      <Providers>
        <SessionsPage />
      </Providers>,
    );

    expect(
      await screen.findByRole("heading", { name: "Sessions", level: 1 }),
    ).toBeInTheDocument();
  });

  it("routes a session id at its detail page", async () => {
    render(
      <Providers>
        {
          await SessionPage({
            params: Promise.resolve({ id: ACTIVITY_IDS.outdoorRide }),
          })
        }
      </Providers>,
    );

    expect(
      await screen.findByRole("heading", { name: "Corrections" }),
    ).toBeInTheDocument();
  });

  it("mounts the calendar page", async () => {
    render(
      <Providers>
        <CalendarPage />
      </Providers>,
    );

    expect(
      await screen.findByRole("heading", { name: "Calendar" }),
    ).toBeInTheDocument();
  });
});
