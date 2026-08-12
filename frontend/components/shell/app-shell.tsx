import type * as React from "react";

import { RedFlagBanner } from "@/components/coach/red-flag";
import { SidebarNav } from "@/components/shell/sidebar-nav";
import { cn } from "@/lib/utils";

/**
 * The application frame: a fixed left sidebar and one scrolling column.
 *
 * Only the right column scrolls, so the nav never moves and a page can put a
 * `sticky top-0` toolbar at the top of its own content (see `Toolbar`).
 *
 * The red-flag banner is above both, spanning the width, and outside the
 * scrolling column so it cannot be scrolled away. It renders nothing
 * while the flag is down, so in the normal case this is the same two-element
 * frame it has always been.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-canvas">
      <RedFlagBanner />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <SidebarNav />
        <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
          {children}
        </div>
      </div>
    </div>
  );
}

/** The 52px bar at the top of a page: title, navigation, page-level actions. */
export function Toolbar({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "sticky top-0 z-20 flex h-[52px] shrink-0 items-center gap-3.5 border-hairline border-b bg-chrome px-[22px]",
        className,
      )}
      {...props}
    />
  );
}

/** The content column under the toolbar, at the mockup's max width and padding. */
export function PageBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "mx-auto w-full max-w-[1400px] px-6 pt-[22px] pb-12",
        className,
      )}
      {...props}
    />
  );
}
