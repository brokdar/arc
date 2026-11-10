"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS, type NavItem } from "@/components/shell/sidebar-nav";
import { cn } from "@/lib/utils";

export interface BottomNavProps {
  /** The rows to choose from — `NAV_ITEMS` everywhere but a test. */
  readonly items?: readonly NavItem[];
}

/**
 * The phone's navigation: a bar along the bottom edge, under 768 px.
 *
 * A separate component rather than a mode of `SidebarNav`, because the two
 * are different layouts — a desktop column of every section against a row of
 * four thumb-sized tabs — but not a separate list: the tabs are the
 * `NAV_ITEMS` rows marked `phone`, in their order there. A section that has
 * not landed is left out rather than drawn dimmed: a phone bar has no room for
 * a tab that does nothing, and no hover to explain why.
 *
 * Every tab is at least 44 × 44 px (`min-h-11 min-w-11`), the smallest target
 * a thumb hits reliably; the bar pads itself above the home indicator with
 * the safe-area inset.
 */
export function BottomNav({ items = NAV_ITEMS }: BottomNavProps = {}) {
  const pathname = usePathname();
  const tabs = items.filter((item) => item.phone && item.ready);

  return (
    <nav
      aria-label="Sections (phone)"
      className="shrink-0 border-hairline border-t bg-chrome pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <ul className="flex">
        {tabs.map((item) => {
          const Icon = item.icon;
          // Prefix match, as in the sidebar: `/sessions/abc` is inside
          // Sessions, not somewhere else.
          const active =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <li key={item.href} className="flex flex-1">
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-11 min-w-11 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 font-[450] text-xs transition-colors",
                  active ? "text-ink" : "text-ink-secondary hover:text-ink",
                )}
              >
                <Icon />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
