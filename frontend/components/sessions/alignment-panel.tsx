"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { Field } from "@/components/design/field";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { formatDurationClock } from "@/lib/format";
import {
  alignmentLists,
  excludedReason,
  formatConfidence,
} from "@/lib/scoring";

/** `app.services.scoring.MAX_ALIGNMENT_OFFSET_S`: six hours either way. */
export const MAX_OFFSET_S = 6 * 60 * 60;

export interface AlignmentPanelProps {
  readonly sessionId: string;
}

/**
 * Which detected effort answered which prescribed step, and the one control
 * that changes it (A7.1).
 *
 * The offset is **functional, not cosmetic**: sliding the planned timeline
 * changes which effort is paired with which step, so it changes the adherence
 * and pacing axes above. Setting it writes a new alignment version and a new
 * score version in one transaction, which is why this component invalidates
 * the score as well as itself — a page showing yesterday's axes beside today's
 * pairing would be showing two different judgements at once.
 *
 * Three lists, and they mean three different things. An **aligned** pair is a
 * step that was performed. An **excluded** pair is one the assignment made and
 * the confidence gate refused — "we matched this step to that effort and did
 * not trust the match", which is not the same as never having done it. An
 * **unmatched** step is the one that was never performed at all.
 *
 * Absent for a session with no alignment: a strength session's sets are paired
 * by position rather than on a timeline, and the API says so in the 404 this
 * renders.
 */
export function AlignmentPanel({ sessionId }: AlignmentPanelProps) {
  const base = useId();
  const queryClient = useQueryClient();
  const path = { params: { path: { session_id: sessionId } } };
  const alignment = $api.useQuery(
    "get",
    "/api/v1/sessions/{session_id}/alignment",
    path,
  );
  const [offset, setOffset] = useState("");

  const inForce = alignment.data?.offset_s ?? null;
  // Follow the alignment rather than keep what was typed: an offset that
  // landed is the new truth, and the field would otherwise go on offering a
  // correction that has already been made.
  useEffect(() => {
    if (inForce !== null) {
      setOffset(String(inForce));
    }
  }, [inForce]);

  const slide = $api.useMutation(
    "put",
    "/api/v1/sessions/{session_id}/alignment",
    {
      onSuccess: () => {
        for (const key of [
          $api.queryOptions(
            "get",
            "/api/v1/sessions/{session_id}/alignment",
            path,
          ).queryKey,
          $api.queryOptions("get", "/api/v1/sessions/{session_id}/score", path)
            .queryKey,
        ]) {
          queryClient.invalidateQueries({ queryKey: key });
        }
      },
    },
  );

  if (alignment.isPending) {
    return null;
  }

  if (alignment.error || !alignment.data) {
    return (
      <Section>
        <Panel className="px-5 py-4 text-ink-muted text-base">
          {apiErrorMessages(alignment.error)[0] ??
            "This session has no alignment."}
        </Panel>
      </Section>
    );
  }

  const held = alignment.data;
  const lists = alignmentLists(held);
  const parsed = Number.parseInt(offset, 10);
  const valid = Number.isFinite(parsed) && Math.abs(parsed) <= MAX_OFFSET_S;
  const problems = apiErrorMessages(slide.error);

  return (
    <Section>
      <Panel className="flex flex-col gap-3.5 px-5 py-4">
        <p className="max-w-[68ch] text-ink-muted text-sm">
          The planned timeline slid by{" "}
          <span className="font-mono text-ink">{signed(held.offset_s)}</span>{" "}
          before the steps were assigned — positive means the workout began
          later than the recording did. Correcting it re-pairs the efforts and
          rescores the session.
        </p>

        <form
          className="flex flex-wrap items-end gap-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) {
              slide.mutate({ ...path, body: { offset_s: parsed } });
            }
          }}
        >
          <Field
            label="Offset"
            hint="seconds, ±"
            htmlFor={`${base}-offset`}
            className="w-[150px]"
          >
            <Input
              id={`${base}-offset`}
              type="number"
              step={30}
              min={-MAX_OFFSET_S}
              max={MAX_OFFSET_S}
              value={offset}
              className="font-mono"
              onChange={(event) => setOffset(event.target.value)}
            />
          </Field>
          <Button
            type="submit"
            variant="secondary"
            disabled={!valid || parsed === held.offset_s || slide.isPending}
          >
            Re-align and rescore
          </Button>
          <span className="font-mono text-2xs text-ink-faint">
            version {held.version}
          </span>
        </form>

        {problems.length > 0 ? (
          <ul
            role="alert"
            className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
          >
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}

        <table
          data-testid="alignment-table"
          className="w-full border-collapse text-sm"
        >
          <caption className="pb-1.5 text-left">
            <SectionLabel>Planned step ↔ detected effort</SectionLabel>
          </caption>
          <thead>
            <tr className="border-hairline border-b text-left">
              <Th className="w-[92px]">Step</Th>
              <Th className="w-[104px]">Effort</Th>
              <Th className="w-[92px]">Confidence</Th>
              <Th>What that means</Th>
            </tr>
          </thead>
          <tbody>
            {lists.aligned.map((pair) => (
              <Row
                key={`aligned-${pair.step_index}`}
                step={pair.step_index}
                effort={pair.interval_index}
                confidence={pair.confidence}
                detail="Paired."
              />
            ))}
            {lists.excluded.map((pair) => (
              <Row
                key={`excluded-${pair.step_index}`}
                step={pair.step_index}
                effort={pair.interval_index}
                confidence={pair.confidence}
                detail={excludedReason(pair.reason)}
                muted
              />
            ))}
            {lists.unmatchedSteps.map((step) => (
              <Row
                key={`unmatched-${step}`}
                step={step}
                effort={null}
                confidence={null}
                detail="No detected effort answered this step."
                muted
              />
            ))}
            {lists.unmatchedIntervals.map((interval) => (
              <Row
                key={`extra-${interval}`}
                step={null}
                effort={interval}
                confidence={null}
                detail="An effort the prescription did not ask for."
                muted
              />
            ))}
          </tbody>
        </table>

        {lists.aligned.length === 0 &&
        lists.excluded.length === 0 &&
        lists.unmatchedSteps.length === 0 &&
        lists.unmatchedIntervals.length === 0 ? (
          <p className="text-ink-muted text-sm">
            This prescription has no work steps to pair — there is nothing on
            the timeline for a detected effort to answer.
          </p>
        ) : null}
      </Panel>
    </Section>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Alignment</SectionLabel>
      {children}
    </section>
  );
}

function Th({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <th
      scope="col"
      className={`px-1 py-1.5 font-semibold text-ink-faint text-label uppercase tracking-[0.09em] ${className ?? ""}`}
    >
      {children}
    </th>
  );
}

/** One row of the pairing. Every column keeps its place, filled or not. */
function Row({
  step,
  effort,
  confidence,
  detail,
  muted = false,
}: {
  step: number | null;
  effort: number | null;
  confidence: number | null;
  detail: string;
  muted?: boolean;
}) {
  return (
    <tr className="border-hairline-faint border-b last:border-b-0">
      <td className="px-1 py-1.5 font-mono text-ink text-sm">
        {step === null ? <span className="text-ink-faint">—</span> : step + 1}
      </td>
      <td className="px-1 py-1.5 font-mono text-ink text-sm">
        {effort === null ? (
          <span className="text-ink-faint">—</span>
        ) : (
          effort + 1
        )}
      </td>
      <td className="px-1 py-1.5 font-mono text-ink-secondary text-sm">
        {confidence === null ? (
          <span className="text-ink-faint">—</span>
        ) : (
          formatConfidence(confidence)
        )}
      </td>
      <td
        className={`px-1 py-1.5 text-sm ${muted ? "text-ink-muted" : "text-ink-secondary"}`}
      >
        {detail}
      </td>
    </tr>
  );
}

/** `+8:00` / `−20:00` / `0` — an offset reads as a signed clock. */
function signed(seconds: number): string {
  if (seconds === 0) {
    return "0 s";
  }
  const sign = seconds < 0 ? "−" : "+";
  return `${sign}${formatDurationClock(Math.abs(seconds))}`;
}
