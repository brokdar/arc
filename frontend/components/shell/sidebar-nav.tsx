"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  AnalysisIcon,
  CalendarIcon,
  CoachIcon,
  type IconProps,
  InboxIcon,
  SessionsIcon,
  SettingsIcon,
  TodayIcon,
  WorkoutsIcon,
} from "@/components/icons";
import { $api } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** The section whose nav row carries a count of what is waiting. */
const COUNTED_HREF = "/proposals";

interface NavItem {
  readonly href: string;
  readonly label: string;
  readonly icon: (props: IconProps) => React.ReactElement;
  /**
   * A route that does not exist yet renders dimmed and inert rather than as a
   * link to a 404 — the mockup's own treatment for the sections it previews.
   * Flip one flag when the page lands.
   */
  readonly ready: boolean;
  /**
   * What a section that is not ready is waiting for, named by work package.
   *
   * Shown on hover, so the sidebar answers "when?" instead of implying the
   * feature is broken. Required for every unready item and forbidden on a
   * ready one — a stale "arrives soon" on a page that shipped is worse than
   * no tooltip at all (D61).
   */
  readonly arrives?: string;
}

/**
 * The sections of the app, in the order the sidebar lists them.
 *
 * All of them the mockup previews, not just the ones that exist: the shape of
 * the application is part of what the shell communicates, and a nav that grows
 * a row per release reads as a different product each time (D61). The ones
 * that have no page render dimmed and inert.
 *
 * Inbox is the eighth and is not one the mockup drew: the watched folder needs
 * a queue the athlete can answer, and it sits *after* Sessions because that is
 * the direction the athlete travels — you go looking for the inbox because of
 * a ride that is missing from the log, not the other way round.
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/today", label: "Today", icon: TodayIcon, ready: true },
  { href: "/calendar", label: "Calendar", icon: CalendarIcon, ready: true },
  { href: "/sessions", label: "Sessions", icon: SessionsIcon, ready: true },
  { href: "/inbox", label: "Inbox", icon: InboxIcon, ready: true },
  { href: "/workouts", label: "Workouts", icon: WorkoutsIcon, ready: true },
  {
    href: "/analysis",
    label: "Analysis",
    icon: AnalysisIcon,
    ready: false,
    arrives:
      "Per-session analysis is on each session page; the aggregate surface — power curves, trends — arrives after the MVP",
  },
  // The row the mockup reserved for "Coach", resolved into the one coach
  // surface that is a *place*: the queue of plan changes waiting on an answer
  // (D181). The coach's other output — its notes on a session or a week — is
  // read where the thing it is about is, not in a section of its own.
  { href: "/proposals", label: "Proposals", icon: CoachIcon, ready: true },
  { href: "/settings", label: "Settings", icon: SettingsIcon, ready: true },
];

export function SidebarNav() {
  const pathname = usePathname();
  // One page of one, asked for its `total`: the nav needs the count and not
  // the proposals, and the alternative — a full page fetched on every screen
  // of the app to render a two-digit badge — is the expensive version of the
  // same answer.
  const pending = $api.useQuery("get", "/api/v1/proposals", {
    params: { query: { status: "pending", limit: 1 } },
  });
  const waiting = pending.data?.total ?? 0;

  return (
    <nav
      aria-label="Sections"
      className="flex w-[212px] shrink-0 flex-col gap-5 border-hairline border-r bg-chrome px-3 pt-[18px] pb-3.5"
    >
      <Link
        href="/calendar"
        className="flex items-center gap-2.5 px-2 py-0 text-ink"
      >
        <span
          aria-hidden
          className="flex size-[22px] items-center justify-center rounded-md bg-accent"
        >
          <span className="size-2 rounded-[2px] bg-canvas" />
        </span>
        <span className="font-semibold text-lg tracking-[-0.01em]">arc</span>
      </Link>

      <ul className="flex flex-col gap-px">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          // Prefix match, so `/workouts/new` still lights up "Workouts": a
          // nested route is somewhere *inside* a section, not somewhere else.
          const active =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          const badge = item.href === COUNTED_HREF && waiting > 0 ? waiting : 0;
          return (
            <li key={item.href}>
              {item.ready ? (
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  // The count is spoken as what it counts. The bare numeral is
                  // legible beside the word on screen and meaningless read out
                  // after it, and both the label and the figure stay inside
                  // the spoken name (WCAG 2.5.3).
                  aria-label={
                    badge
                      ? `${item.label} — ${badge} waiting on you`
                      : undefined
                  }
                  className={cn(
                    "flex items-center gap-2.5 rounded-button px-2.5 py-[7px] font-[450] text-base transition-colors",
                    // The section you are on is a *state*, not a hover: the
                    // mockup draws it one step lighter than the hover tint so
                    // the two never read as the same thing.
                    active
                      ? "bg-chrome-active text-ink"
                      : "text-ink-secondary hover:bg-card-hover hover:text-ink",
                  )}
                >
                  <Icon />
                  {item.label}
                  {badge ? (
                    <span
                      aria-hidden
                      data-testid="pending-proposals"
                      className="ml-auto rounded-badge bg-coach-tint px-1.5 py-0.5 font-mono text-2xs text-coach-strong"
                    >
                      {badge}
                    </span>
                  ) : null}
                </Link>
              ) : (
                <span
                  data-ready="false"
                  aria-disabled="true"
                  title={item.arrives}
                  className="flex cursor-default items-center gap-2.5 rounded-button px-2.5 py-[7px] font-[450] text-base text-ink-disabled"
                >
                  <Icon />
                  {item.label}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
