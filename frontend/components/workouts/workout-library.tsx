"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { WorkoutProfileBars } from "@/components/design/workout-profile-bars";
import { DisciplineIcon } from "@/components/icons";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { formatDurationHm, formatSets } from "@/lib/format";

type Workout = components["schemas"]["WorkoutRead"];

/** How long the search box waits for the typing to stop, in milliseconds. */
const SEARCH_DEBOUNCE_MS = 250;

/** A value that follows its input, `delay` ms behind. */
function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/**
 * The workout library: everything the athlete has written, ready to be planned.
 *
 * Filtering happens on the server (`q`, `folder`, `tag`, `discipline` are all
 * query parameters the list endpoint understands) rather than over a page of
 * results, so the box keeps working once the library is longer than one page.
 *
 * Note what a card does *not* show: a purpose badge. A workout has no purpose
 * in the model — purpose is a property of *planning* a session, not of the
 * prescription — so the card shows the discipline, the shape of the
 * prescription, and the labels the athlete filed it under.
 */
export function WorkoutLibrary() {
  const base = useId();
  const [search, setSearch] = useState("");
  const [folder, setFolder] = useState("");
  const [tag, setTag] = useState("");

  // The box is uncontrolled by the query: typing "endurance" would otherwise
  // be nine requests and nine renders of a list that changes under the
  // cursor, and react-query cannot coalesce them because each keystroke is a
  // different key. A quarter of a second is below the threshold at which a
  // search feels delayed and above the interval between keystrokes.
  const query = useDebounced(search.trim(), SEARCH_DEBOUNCE_MS);

  const labels = $api.useQuery("get", "/api/v1/workout-labels");
  const workouts = $api.useQuery("get", "/api/v1/workouts", {
    params: {
      query: {
        ...(query ? { q: query } : {}),
        ...(folder ? { folder } : {}),
        ...(tag ? { tag } : {}),
        limit: 100,
      },
    },
  });

  const items = workouts.data?.items ?? [];
  const filtered = query !== "" || folder !== "" || tag !== "";

  return (
    <>
      <Toolbar>
        {/* The page's one `h1`: every route owns exactly one, and the cards
            below are links rather than headings. */}
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Workouts</h1>
        <span className="font-mono text-ink-muted text-sm">
          {workouts.data ? `${workouts.data.total} in the library` : ""}
        </span>
        <div className="ml-auto">
          <Button
            size="sm"
            render={<Link href="/workouts/new">New workout</Link>}
          />
        </div>
      </Toolbar>

      <PageBody className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-2.5">
          <div className="flex min-w-[220px] flex-1 flex-col gap-1">
            <label
              htmlFor={`${base}-search`}
              className="text-ink-muted text-xs"
            >
              Search
            </label>
            <Input
              id={`${base}-search`}
              type="search"
              placeholder="Name or description…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>

          <div className="flex w-[170px] flex-col gap-1">
            <label
              htmlFor={`${base}-folder`}
              className="text-ink-muted text-xs"
            >
              Folder
            </label>
            <NativeSelect
              className="w-full"
              id={`${base}-folder`}
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
            >
              <NativeSelectOption value="">All folders</NativeSelectOption>
              {(labels.data?.folders ?? []).map((name) => (
                <NativeSelectOption key={name} value={name}>
                  {name}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>

          <div className="flex w-[170px] flex-col gap-1">
            <label htmlFor={`${base}-tag`} className="text-ink-muted text-xs">
              Tag
            </label>
            <NativeSelect
              className="w-full"
              id={`${base}-tag`}
              value={tag}
              onChange={(event) => setTag(event.target.value)}
            >
              <NativeSelectOption value="">All tags</NativeSelectOption>
              {(labels.data?.tags ?? []).map((name) => (
                <NativeSelectOption key={name} value={name}>
                  {name}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>
        </div>

        {workouts.isPending ? (
          <p className="text-ink-muted text-sm">Loading the library…</p>
        ) : workouts.error ? (
          <p role="alert" className="text-destructive text-sm">
            Could not load the library. Is the API reachable?
          </p>
        ) : items.length === 0 ? (
          <EmptyLibrary filtered={filtered} />
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((workout) => (
              <li key={workout.id}>
                <WorkoutCard workout={workout} />
              </li>
            ))}
          </ul>
        )}
      </PageBody>
    </>
  );
}

function WorkoutCard({ workout }: { workout: Workout }) {
  const cycling = workout.discipline === "cycling";
  const measure = cycling
    ? formatDurationHm(workout.summary.total_duration_s)
    : formatSets(workout.summary.total_sets);

  return (
    <Link
      href={`/workouts/${workout.id}`}
      className="flex h-full flex-col gap-2 rounded-card border border-hairline-card bg-card px-3.5 py-3 transition-colors hover:bg-card-hover focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
    >
      <span className="flex items-center gap-1.5 text-ink-muted">
        <DisciplineIcon discipline={workout.discipline} size={12} />
        <span className="font-mono text-xs">{measure}</span>
        {cycling && workout.summary.step_count > 0 ? (
          <span className="ml-auto font-mono text-2xs text-ink-faint">
            {workout.summary.step_count} steps
          </span>
        ) : null}
      </span>

      <span className="font-medium text-base text-ink leading-tight">
        {workout.name}
      </span>

      {workout.description ? (
        <span className="line-clamp-2 text-ink-muted text-xs leading-snug">
          {workout.description}
        </span>
      ) : null}

      {cycling ? <WorkoutProfileBars structure={workout.structure} /> : null}

      {workout.folder || workout.tags.length > 0 ? (
        <span className="mt-auto flex flex-wrap items-center gap-1.5 pt-0.5">
          {workout.folder ? (
            <span className="rounded-badge bg-raised px-1.5 py-0.5 text-2xs text-ink-secondary">
              {workout.folder}
            </span>
          ) : null}
          {workout.tags.map((label) => (
            <span
              key={label}
              className="rounded-badge border border-hairline px-1.5 py-0.5 text-2xs text-ink-muted"
            >
              {label}
            </span>
          ))}
        </span>
      ) : null}
    </Link>
  );
}

/** An empty state names the missing input and the control that supplies it. */
function EmptyLibrary({ filtered }: { filtered: boolean }) {
  return (
    <Panel className="flex flex-col items-start gap-2.5 px-5 py-6">
      <SectionLabel level={2}>
        {filtered ? "Nothing matches" : "The library is empty"}
      </SectionLabel>
      <p className="max-w-[46ch] text-ink-muted text-base">
        {filtered
          ? "No workout matches that search. Clear the filters, or write a new one."
          : "A workout is a prescription you can plan onto any day — a step tree for a ride, sets and reps for a lift. Write one and it becomes available to the calendar."}
      </p>
      <Button
        size="sm"
        render={<Link href="/workouts/new">New workout</Link>}
      />
    </Panel>
  );
}
