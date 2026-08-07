import { render, screen } from "@testing-library/react";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { SidebarNav } from "@/components/shell/sidebar-nav";

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
});
