"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  CalendarIcon,
  type IconProps,
  TodayIcon,
  WorkoutsIcon,
} from "@/components/icons";
import { cn } from "@/lib/utils";

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
}

/** The sections of the app, in the order the sidebar lists them. */
export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/today", label: "Today", icon: TodayIcon, ready: true },
  { href: "/calendar", label: "Calendar", icon: CalendarIcon, ready: true },
  { href: "/workouts", label: "Workouts", icon: WorkoutsIcon, ready: true },
];

export function SidebarNav() {
  const pathname = usePathname();

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
          return (
            <li key={item.href}>
              {item.ready ? (
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-button px-2.5 py-[7px] font-[450] text-base transition-colors",
                    active
                      ? "bg-card-hover text-ink"
                      : "text-ink-secondary hover:bg-card-hover hover:text-ink",
                  )}
                >
                  <Icon />
                  {item.label}
                </Link>
              ) : (
                <span
                  aria-disabled="true"
                  title="Arrives with the next slice of WP-3"
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
