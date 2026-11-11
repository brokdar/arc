import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/app-shell";
import { BottomNav } from "@/components/shell/bottom-nav";
import { NAV_ITEMS } from "@/components/shell/sidebar-nav";
import { translate } from "@/lib/i18n/catalogue";

const pathname = vi.hoisted(() => ({ current: "/today" }));

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

/**
 * The phone's bar and the desktop's sidebar draw the same list.
 *
 * Which sections reach the bar is a property of the item in `NAV_ITEMS`
 * (`phone: true`), not a second list in `bottom-nav.tsx`: a second list is a
 * section renamed in one nav and not the other. These tests read the bar's
 * rows back out of `NAV_ITEMS`, so a copy that drifted would fail them.
 *
 * jsdom applies no stylesheet, so the 768 px switch is asserted here on the
 * classes that make it and measured in a real browser in
 * `e2e/phone-nav.spec.ts`.
 */
function renderShell() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppShell>
        <p>page</p>
      </AppShell>
    </QueryClientProvider>,
  );
}

describe("BottomNav", () => {
  it("steps aside on the logging screen, whose ✓ owns the bottom edge", () => {
    pathname.current = "/train/0199c000-0000-7000-8000-00000000a001";
    render(<BottomNav />);

    expect(
      screen.queryByRole("navigation", { name: "Sections (phone)" }),
    ).not.toBeInTheDocument();
  });

  it("shows Today, Calendar, Sessions and Settings, in that order", () => {
    pathname.current = "/today";
    render(<BottomNav />);

    const bar = screen.getByRole("navigation", { name: "Sections (phone)" });
    expect(
      within(bar)
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["Today", "/today"],
      ["Calendar", "/calendar"],
      ["Sessions", "/sessions"],
      ["Settings", "/settings"],
    ]);
  });

  it("draws its rows from NAV_ITEMS, not a list of its own", () => {
    render(<BottomNav />);

    const fromNavItems = NAV_ITEMS.filter((item) => item.phone).map((item) =>
      translate("en", item.label),
    );
    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual(
      fromNavItems,
    );
  });

  it("marks the route you are on, including a route inside it", () => {
    pathname.current = "/sessions/abc";
    render(<BottomNav />);

    expect(screen.getByRole("link", { name: "Sessions" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Today" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("leaves out a section whose page has not landed", () => {
    const icon = NAV_ITEMS[0]?.icon ?? (() => <span />);
    render(
      <BottomNav
        items={[
          ...NAV_ITEMS,
          {
            href: "/later",
            label: "nav.inbox",
            icon,
            ready: false,
            arrives: "Arrives later",
            phone: true,
          },
        ]}
      />,
    );

    expect(screen.queryByText("Inbox")).not.toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(4);
  });

  it("gives every tab a target at least 44 px tall and wide", () => {
    render(<BottomNav />);

    for (const link of screen.getAllByRole("link")) {
      // `min-h-11` / `min-w-11` are 2.75rem = 44 px at the root size.
      expect(link).toHaveClass("min-h-11", "min-w-11");
    }
  });
});

describe("the shell at both widths", () => {
  it("shows the bottom bar only under 768 px and the sidebar only from it", () => {
    renderShell();

    const bar = screen.getByRole("navigation", { name: "Sections (phone)" });
    const sidebar = screen.getByRole("navigation", { name: "Sections" });
    expect(bar).toHaveClass("md:hidden");
    expect(bar).not.toHaveClass("hidden");
    expect(sidebar).toHaveClass("hidden", "md:flex");
  });
});
