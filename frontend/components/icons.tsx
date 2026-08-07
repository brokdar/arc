import type * as React from "react";

/**
 * The icon set, as inline SVG.
 *
 * Hand-drawn on a 16×16 grid with a 1.3 stroke, the way the mockup draws them:
 * every glyph is one or two primitives, `stroke="currentColor"`, no fills
 * except deliberate dots. Inline rather than from `lucide-react` because the
 * two disciplines (a bike, a barbell) have no faithful equivalent there and a
 * set that is half hand-drawn and half library reads as two sets.
 *
 * All of them are decorative: they sit next to text that already says what
 * they mean. `Glyph` carries the `aria-hidden` for every one of them, which is
 * also what keeps each icon down to its paths.
 */

export interface IconProps {
  readonly className?: string;
  readonly size?: number;
}

/** The shared `<svg>` frame. Decorative by construction. */
function Glyph({
  className,
  size = 15,
  children,
}: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      className={className}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
    >
      {children}
    </svg>
  );
}

/** Today — a target with a centre. */
export function TodayIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="8" r="1.8" fill="currentColor" />
    </Glyph>
  );
}

/** Calendar — a page with a header rule. */
export function CalendarIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect
        x="2"
        y="3.2"
        width="12"
        height="10.5"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <path d="M2 6.6h12" stroke="currentColor" strokeWidth="1.3" />
    </Glyph>
  );
}

/** Workouts — a bar chart. */
export function WorkoutsIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="2.4" y="7" width="2.2" height="5" rx="1" fill="currentColor" />
      <rect
        x="6.9"
        y="3.4"
        width="2.2"
        height="8.6"
        rx="1"
        fill="currentColor"
      />
      <rect
        x="11.4"
        y="5.4"
        width="2.2"
        height="6.6"
        rx="1"
        fill="currentColor"
      />
    </Glyph>
  );
}

/** Sessions — a list of rules, the last one short. */
export function SessionsIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path
        d="M3 4.5h10M3 8h10M3 11.5h6"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </Glyph>
  );
}

/** Inbox — a tray with the notch a file drops through. */
export function InboxIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path
        d="M4 3.4h8l2 6v2.2a1.4 1.4 0 0 1-1.4 1.4H3.4A1.4 1.4 0 0 1 2 11.6V9.4z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path
        d="M2 9.4h3.2l.9 1.6h3.8l.9-1.6H14"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Analysis — a trend line with two turns. */
export function AnalysisIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path
        d="M2.5 11.5L6 7.5l3 2.6 4.5-6"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

/** Settings — a dial inside a dashed ring. */
export function SettingsIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="2.4" stroke="currentColor" strokeWidth="1.3" />
      <circle
        cx="8"
        cy="8"
        r="5.6"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeDasharray="2.2 2.4"
      />
    </Glyph>
  );
}

/** Cycling — two wheels and a frame. */
export function BikeIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="4" cy="11" r="2.6" stroke="currentColor" strokeWidth="1.2" />
      <circle cx="12" cy="11" r="2.6" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M4 11l3-5h4"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </Glyph>
  );
}

/** Strength — a loaded bar. */
export function BarbellIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect
        x="2"
        y="6"
        width="12"
        height="4"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <circle cx="4.5" cy="8" r="1" fill="currentColor" />
      <circle cx="11.5" cy="8" r="1" fill="currentColor" />
    </Glyph>
  );
}

/** The coach's face. Reserved for the agent surfaces (WP-8). */
export function CoachIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="5.8" cy="7.4" r="1" fill="currentColor" />
      <circle cx="10.2" cy="7.4" r="1" fill="currentColor" />
    </Glyph>
  );
}

export function ChevronLeftIcon({ size = 12, ...props }: IconProps) {
  return (
    <Glyph size={size} {...props}>
      <path
        d="M9.9 3.2L5 8l4.9 4.8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

export function ChevronRightIcon({ size = 12, ...props }: IconProps) {
  return (
    <Glyph size={size} {...props}>
      <path
        d="M6.1 3.2L11 8l-4.9 4.8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Glyph>
  );
}

export function CloseIcon({ size = 12, ...props }: IconProps) {
  return (
    <Glyph size={size} {...props}>
      <path
        d="M3.6 3.6l8.8 8.8M12.4 3.6l-8.8 8.8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </Glyph>
  );
}

export function PauseIcon({ size = 12, ...props }: IconProps) {
  return (
    <Glyph size={size} {...props}>
      <rect x="4" y="3.2" width="2.6" height="9.6" rx="1" fill="currentColor" />
      <rect
        x="9.4"
        y="3.2"
        width="2.6"
        height="9.6"
        rx="1"
        fill="currentColor"
      />
    </Glyph>
  );
}

export function PlayIcon({ size = 12, ...props }: IconProps) {
  return (
    <Glyph size={size} {...props}>
      <path d="M4.6 3.2L12.4 8l-7.8 4.8z" fill="currentColor" />
    </Glyph>
  );
}

/** The discipline glyph for a session. */
export function DisciplineIcon({
  discipline,
  ...props
}: IconProps & { discipline: "cycling" | "strength" }) {
  return discipline === "cycling" ? (
    <BikeIcon {...props} />
  ) : (
    <BarbellIcon {...props} />
  );
}
