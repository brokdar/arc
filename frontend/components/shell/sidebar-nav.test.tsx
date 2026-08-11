import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS, SidebarNav } from "@/components/shell/sidebar-nav";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

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

/**
 * The nav counts what is waiting, so it needs a query client.
 *
 * Its own, per render, rather than a module-level one: the badge is read off a
 * cached count, and a client shared between tests would carry the previous
 * test's answer into the next one's first paint.
 */
function renderNav() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SidebarNav />
    </QueryClientProvider>,
  );
}

describe("SidebarNav", () => {
  it("marks the section you are on", () => {
    pathname.current = "/calendar";
    renderNav();

    expect(screen.getByRole("link", { name: "Calendar" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("links every section whose page has landed", () => {
    renderNav();

    for (const [label, href] of [
      ["Today", "/today"],
      ["Calendar", "/calendar"],
      ["Sessions", "/sessions"],
      ["Inbox", "/inbox"],
      ["Workouts", "/workouts"],
      ["Proposals", "/proposals"],
      ["Settings", "/settings"],
    ] as const) {
      expect(
        screen.getByRole("link", { name: new RegExp(`^${label}`) }),
      ).toHaveAttribute("href", href);
    }
  });

  it("marks the section a nested route belongs to", () => {
    pathname.current = "/workouts/new";
    renderNav();

    expect(screen.getByRole("link", { name: "Workouts" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("carries the wordmark back to the calendar", () => {
    renderNav();

    expect(screen.getByRole("link", { name: "arc" })).toHaveAttribute(
      "href",
      "/calendar",
    );
  });

  it("lists all eight sections the app is going to have", () => {
    renderNav();

    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Today",
      "Calendar",
      "Sessions",
      "Inbox",
      "Workouts",
      "Analysis",
      "Proposals",
      "Settings",
    ]);
    for (const item of NAV_ITEMS) {
      expect(screen.getByText(item.label)).toBeInTheDocument();
    }
  });

  it("marks the section the current active item apart from a hover", () => {
    pathname.current = "/calendar";
    renderNav();

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

describe("the count of what is waiting", () => {
  it("badges Proposals with the number of pending ones", async () => {
    renderNav();

    // The seed carries one pending proposal and four resolved ones; the badge
    // is the count of the first kind, not of all five.
    const badge = await screen.findByTestId("pending-proposals");
    expect(badge).toHaveTextContent("1");
    // Spoken as what it counts, not as a bare numeral after the label.
    expect(
      screen.getByRole("link", { name: "Proposals — 1 waiting on you" }),
    ).toHaveAttribute("href", "/proposals");
  });

  it("draws no badge when nothing is waiting", async () => {
    server.use(
      http.get("/api/v1/proposals", ({ response }) =>
        response(200).json({ items: [], total: 0, offset: 0, limit: 1 }),
      ),
    );
    renderNav();

    // The link is there either way — the badge is the only thing that comes
    // and goes, because a nav row that appeared with a number would move
    // every other row down the moment the coach said something.
    expect(
      await screen.findByRole("link", { name: "Proposals" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("pending-proposals")).not.toBeInTheDocument();
  });
});

describe("a section whose page has not landed", () => {
  const unready = NAV_ITEMS.filter((item) => !item.ready);

  it("previews the one section that has not landed", () => {
    expect(unready.map((item) => item.label)).toEqual(["Analysis"]);
  });

  it.each(unready)("renders $label dimmed and inert", (item) => {
    renderNav();

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
    renderNav();

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
