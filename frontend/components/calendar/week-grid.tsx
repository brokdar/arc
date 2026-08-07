"use client";

import { useState } from "react";

import {
  SESSION_DRAG_TYPE,
  SessionCard,
  type WeekSession,
} from "@/components/calendar/session-card";
import type { components } from "@/generated/api/schema";
import { weekdayLabel } from "@/lib/dates";
import { formatDayMonth } from "@/lib/format";
import { cn } from "@/lib/utils";

type PlanWeekDay = components["schemas"]["PlanWeekDayRead"];

export interface WeekGridProps {
  readonly days: readonly PlanWeekDay[];
  /** The athlete's local today, so the column can carry the accent treatment. */
  readonly today: string;
  readonly onOpen: (session: WeekSession) => void;
  /** Called when a card is dropped on a *different* day. */
  readonly onMove: (sessionId: string, toDate: string) => void;
}

/** Seven Monday-first columns. The week endpoint always returns seven days. */
export function WeekGrid({ days, today, onOpen, onMove }: WeekGridProps) {
  const [dragging, setDragging] = useState(false);

  return (
    <div className="grid grid-cols-[repeat(7,minmax(134px,1fr))] gap-2 overflow-x-auto pb-1.5">
      {days.map((day) => (
        <DayColumn
          key={day.date}
          day={day}
          isToday={day.date === today}
          dragInProgress={dragging}
          onOpen={onOpen}
          onMove={onMove}
          onDragStateChange={setDragging}
        />
      ))}
    </div>
  );
}

interface DayColumnProps {
  readonly day: PlanWeekDay;
  readonly isToday: boolean;
  readonly dragInProgress: boolean;
  readonly onOpen: (session: WeekSession) => void;
  readonly onMove: (sessionId: string, toDate: string) => void;
  readonly onDragStateChange: (dragging: boolean) => void;
}

function DayColumn({
  day,
  isToday,
  dragInProgress,
  onOpen,
  onMove,
  onDragStateChange,
}: DayColumnProps) {
  const [over, setOver] = useState(false);

  function readSessionId(transfer: DataTransfer | null): string {
    if (!transfer) {
      return "";
    }
    return (
      transfer.getData(SESSION_DRAG_TYPE) ||
      transfer.getData("text/plain") ||
      ""
    );
  }

  return (
    <section
      aria-label={`${weekdayLabel(day.date)} ${formatDayMonth(day.date)}`}
      data-testid={`day-${day.date}`}
      data-drop-target={over ? "true" : undefined}
      onDragOver={(event) => {
        // Without preventDefault the browser refuses the drop outright.
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        onDragStateChange(false);
        const sessionId = readSessionId(event.dataTransfer);
        if (sessionId) {
          onMove(sessionId, day.date);
        }
      }}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-card p-1 transition-colors",
        over
          ? "bg-[rgb(76_141_255/0.07)] outline-2 outline-accent-border outline-dashed"
          : dragInProgress
            ? "outline-1 outline-hairline outline-dashed"
            : null,
      )}
    >
      <div className="flex items-center justify-between px-0.5 pb-0.5">
        <span
          className={cn(
            "font-semibold text-[11px]",
            isToday ? "text-accent" : "text-ink-secondary",
          )}
        >
          {weekdayLabel(day.date)}
          {isToday ? " · today" : ""}
        </span>
        <span
          className={cn(
            "font-mono text-2xs",
            isToday ? "text-accent" : "text-ink-faint",
          )}
        >
          {formatDayMonth(day.date)}
        </span>
      </div>

      {day.sessions.map((session) => (
        <SessionCard
          key={session.id}
          session={session}
          isToday={isToday}
          onOpen={onOpen}
          onDragStateChange={onDragStateChange}
        />
      ))}

      {day.sessions.length === 0 ? (
        <p className="px-1 py-3 text-2xs text-ink-faint">Rest</p>
      ) : null}
    </section>
  );
}
