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

  it("does not link to a section that has no page yet", () => {
    render(<SidebarNav />);

    expect(screen.queryByRole("link", { name: "Today" })).toBeNull();
    expect(screen.getByText("Today")).toBeInTheDocument();
  });

  it("carries the wordmark back to the calendar", () => {
    render(<SidebarNav />);

    expect(screen.getByRole("link", { name: "arc" })).toHaveAttribute(
      "href",
      "/calendar",
    );
  });
});
