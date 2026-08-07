import { render, screen } from "@testing-library/react";
import { redirect } from "next/navigation";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";
import CalendarPage from "@/app/(app)/calendar/page";
import AppLayout from "@/app/(app)/layout";
import LoginPage from "@/app/login/page";
import Home from "@/app/page";
import { Providers } from "@/app/providers";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/calendar",
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
