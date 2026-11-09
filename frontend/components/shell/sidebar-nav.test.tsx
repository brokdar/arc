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
      ["Fitness", "/fitness"],
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

  it("lists all nine sections the app has", () => {
    renderNav();

    // Fitness takes the row the `/analysis` placeholder held; Power joins it
    // when its page lands, on its own line.
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Today",
      "Calendar",
      "Wellness",
      "Sessions",
      "Inbox",
      "Workouts",
      "Fitness",
      "Proposals",
      "Settings",
    ]);
    for (const item of NAV_ITEMS) {
      expect(screen.getByText(item.label)).toBeInTheDocument();
    }
  });

  it("keeps Fitness lit on a route inside it", () => {
    pathname.current = "/fitness/anything";
    renderNav();

    expect(screen.getByRole("link", { name: "Fitness" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("links Fitness as a ready row with no arrival note", () => {
    pathname.current = "/calendar";
    renderNav();

    const fitness = screen.getByRole("link", { name: "Fitness" });
    expect(fitness).toHaveAttribute("href", "/fitness");
    // A ready row carries no `arrives` tooltip, in the data or on the page.
    expect(fitness).not.toHaveAttribute("title");
    const item = NAV_ITEMS.find((entry) => entry.href === "/fitness");
    expect(item?.ready).toBe(true);
    expect(item?.arrives).toBeUndefined();
  });

  it("no longer carries the /analysis placeholder", () => {
    renderNav();

    expect(NAV_ITEMS.some((item) => item.href === "/analysis")).toBe(false);
    expect(screen.queryByText("Analysis")).not.toBeInTheDocument();
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
  // Every section in `NAV_ITEMS` has landed, so the dimmed treatment is held to
  // its contract against a stand-in — the branch stays for the next section a
  // release previews before its page exists.
  const preview = {
    href: "/later",
    label: "Later",
    icon: NAV_ITEMS[0]?.icon ?? (() => <span />),
    ready: false,
    arrives: "Arrives after the MVP",
  } as const;

  function renderWith(items: typeof NAV_ITEMS) {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <SidebarNav items={items} />
      </QueryClientProvider>,
    );
  }

  it("has none in the shipped nav", () => {
    expect(NAV_ITEMS.filter((item) => !item.ready)).toEqual([]);
  });

  it("renders an unready section dimmed and inert", () => {
    renderWith([...NAV_ITEMS, preview]);

    // Present, so the shape of the app is visible …
    const entry = screen.getByText(preview.label);
    // … but not a link to a route that would 404 …
    expect(
      screen.queryByRole("link", { name: preview.label }),
    ).not.toBeInTheDocument();
    // … and dimmed, which is the mockup's own treatment.
    const row = entry.closest("[data-ready='false']");
    expect(row).toHaveClass("text-ink-disabled");
    expect(row).toHaveAttribute("aria-disabled", "true");
    expect(row).toHaveAttribute("title", preview.arrives);
  });

  it("never leaves an arrival note on a page that shipped", () => {
    for (const item of NAV_ITEMS.filter((entry) => entry.ready)) {
      expect(item.arrives).toBeUndefined();
    }
  });
});
