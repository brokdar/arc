import { render, screen } from "@testing-library/react";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS, SidebarNav } from "@/components/shell/sidebar-nav";

const pathname = vi.hoisted(() => ({ current: "/calendar" }));

vi.mock("next/navigation", () => ({
  usePathname: () => pathname.current,
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

describe("SidebarNav", () => {
  it("marks the section you are on", () => {
    pathname.current = "/calendar";
    render(<SidebarNav />);

    expect(screen.getByRole("link", { name: "Calendar" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("links every section whose page has landed", () => {
    render(<SidebarNav />);

    for (const [label, href] of [
      ["Today", "/today"],
      ["Calendar", "/calendar"],
      ["Workouts", "/workouts"],
    ] as const) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute(
        "href",
        href,
      );
    }
  });

  it("marks the section a nested route belongs to", () => {
    pathname.current = "/workouts/new";
    render(<SidebarNav />);

    expect(screen.getByRole("link", { name: "Workouts" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("carries the wordmark back to the calendar", () => {
    render(<SidebarNav />);

    expect(screen.getByRole("link", { name: "arc" })).toHaveAttribute(
      "href",
      "/calendar",
    );
  });

  it("lists all seven sections the app is going to have", () => {
    render(<SidebarNav />);

    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Today",
      "Calendar",
      "Sessions",
      "Workouts",
      "Analysis",
      "Coach",
      "Settings",
    ]);
    for (const item of NAV_ITEMS) {
      expect(screen.getByText(item.label)).toBeInTheDocument();
    }
  });

  it("marks the section the current active item apart from a hover", () => {
    pathname.current = "/calendar";
    render(<SidebarNav />);

    // The active background is its own token, not the hover tint: two states
    // sharing one colour is two states you cannot tell apart.
    expect(screen.getByRole("link", { name: "Calendar" })).toHaveClass(
      "bg-chrome-active",
    );
    expect(screen.getByRole("link", { name: "Today" })).not.toHaveClass(
      "bg-chrome-active",
    );
  });
});

describe("a section whose page has not landed", () => {
  const unready = NAV_ITEMS.filter((item) => !item.ready);

  it("previews four of them", () => {
    expect(unready.map((item) => item.label)).toEqual([
      "Sessions",
      "Analysis",
      "Coach",
      "Settings",
    ]);
  });

  it.each(unready)("renders $label dimmed and inert", (item) => {
    render(<SidebarNav />);

    // Present, so the shape of the app is visible …
    const entry = screen.getByText(item.label);
    // … but not a link to a route that would 404 …
    expect(
      screen.queryByRole("link", { name: item.label }),
    ).not.toBeInTheDocument();
    // … and dimmed, which is the mockup's own treatment.
    const row = entry.closest("[data-ready='false']");
    expect(row).toHaveClass("text-ink-disabled");
    expect(row).toHaveAttribute("aria-disabled", "true");
  });

  it.each(unready)("says when $label actually arrives", (item) => {
    render(<SidebarNav />);

    const row = screen.getByText(item.label).closest("[data-ready='false']");
    // Names a work package that exists, not "the next slice of" whatever the
    // current one happens to be.
    expect(item.arrives).toMatch(/WP-[45678]|after the MVP/);
    expect(row).toHaveAttribute("title", item.arrives as string);
  });

  it("never leaves an arrival note on a page that shipped", () => {
    for (const item of NAV_ITEMS.filter((entry) => entry.ready)) {
      expect(item.arrives).toBeUndefined();
    }
  });
});
