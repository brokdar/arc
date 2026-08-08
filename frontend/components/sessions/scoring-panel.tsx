"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { DiscardPrompt, useDirtyClose } from "@/components/design/dirty-close";
import { Explained } from "@/components/design/metric-explanation";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { AlignmentPanel } from "@/components/sessions/alignment-panel";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { Session } from "@/lib/activity";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { localStamp } from "@/lib/format";
import {
  AXIS_LABELS,
  AXIS_QUESTIONS,
  CRITERION_LABELS,
  type CriterionOutcome,
  formatAxisValue,
  MAX_REASON_NOTE_CHARS,
  MAX_REASONS,
  REASON_LABELS,
  REASON_ORDER,
  type Reason,
  reasonsProblem,
  resolveAxis,
  type SessionScore,
  scoreLists,
  VERDICT_HINTS,
  VERDICT_LABELS,
  VERDICT_ORDER,
  type Verdict,
  type VerdictDeclaration,
  verdictLabel,
} from "@/lib/scoring";
import { cn } from "@/lib/utils";

export interface ScoringPanelProps {
  readonly session: Session;
}

/**
 * What arc thinks the session was, and what the athlete says it was (WP-7).
 *
 * Two claims, kept visibly apart, because they have different authors. The
 * machine's is a **suggestion** with the rule that produced it printed beside
 * it — a verdict without its reason is the machine asserting rather than
 * showing. The athlete's is a **declaration**, and nothing but the athlete may
 * write it (WP-7.2); once written it stands, even against a later score that
 * disagrees, which is what the contested banner is for (WP-7.4).
 *
 * Every axis holds its slot whether or not it has a number (UI convention 4):
 * an axis with no power meter behind it renders its reason where the
 * percentage would have been, and the grid does not reflow. That is the same
 * `not_assessed(reason)` shape every metric on this page already uses, and it
 * is why an unscored axis can never be read as a zero.
 *
 * A session with no score says which input is missing rather than showing an
 * empty panel: the API's own 404 sentence names it ("a pending proposal is a
 * question, not a link"), and the match panel above is where it is supplied.
 */
export function ScoringPanel({ session }: ScoringPanelProps) {
  const path = { params: { path: { session_id: session.id } } };
  const score = $api.useQuery(
    "get",
    "/api/v1/sessions/{session_id}/score",
    path,
  );
  const verdict = $api.useQuery(
    "get",
    "/api/v1/sessions/{session_id}/verdict",
    path,
  );

  if (score.isPending) {
    return (
      <Section>
        <Panel className="px-5 py-4 text-ink-muted text-base">
          Loading the score…
        </Panel>
      </Section>
    );
  }

  if (score.error || !score.data) {
    return (
      <Section>
        <Panel className="px-5 py-4 text-ink-muted text-base">
          {apiErrorMessages(score.error)[0] ??
            "This session has not been scored."}
        </Panel>
      </Section>
    );
  }

  // A 404 on the verdict is not a failure: it is the ordinary state of a
  // session the athlete has not ruled on yet, which is exactly when the
  // suggestion needs answering.
  const declaration = verdict.data ?? null;

  return (
    <>
      <Section>
        <Panel className="flex flex-col gap-4 px-5 py-4">
          <VerdictBlock
            sessionId={session.id}
            timezone={session.timezone}
            score={score.data}
            declaration={declaration}
          />
          <AxisGrid score={score.data} />
          <CriteriaDetail score={score.data} />
        </Panel>
      </Section>
      <AlignmentPanel sessionId={session.id} />
    </>
  );
}

/** The heading this panel always contributes to the page outline. */
function Section({ children }: { children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Judgement</SectionLabel>
      {children}
    </section>
  );
}

// --- the axes -------------------------------------------------------------

/**
 * One slot per axis the purpose template lists, in the template's order.
 *
 * The positions are fixed and the grid never collapses: a not-assessed axis
 * renders the placeholder carrying its reason in the slot the percentage would
 * have occupied. Position is how a returning eye finds a number, and an axis
 * that vanished when it could not be computed would move every axis after it.
 */
function AxisGrid({ score }: { score: SessionScore }) {
  return (
    <dl
      data-testid="axis-grid"
      className="grid grid-cols-2 gap-x-6 gap-y-3.5 border-hairline border-t pt-3.5 sm:grid-cols-3"
    >
      {scoreLists(score).axes.map((axis) => {
        const resolved = resolveAxis(axis);
        return (
          <div key={axis.axis} className="flex min-w-0 flex-col gap-1">
            <dt>
              {/* An axis this build has no label for prints its own name
                  rather than nothing: a backend that gains an axis should show
                  up as an unfamiliar word, not as a blank slot. */}
              <SectionLabel title={AXIS_QUESTIONS[axis.axis]}>
                {AXIS_LABELS[axis.axis] ?? axis.axis}
              </SectionLabel>
            </dt>
            <dd className="font-mono text-ink text-lg">
              {resolved.kind === "absent" ? (
                <NotAssessed reason={resolved.reason} symbol="?" />
              ) : (
                <Explained explanation={resolved.explanation}>
                  {formatAxisValue(resolved.value)}
                </Explained>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/**
 * The criteria the prescription froze, checked — expandable, one block per axis.
 *
 * Collapsed by default and *below* the grid rather than inside it: the numbers
 * are the summary and the criteria are the working, and putting the working in
 * the cells would make the grid reflow the moment one was opened.
 *
 * `other_criteria` are criteria whose own axis this purpose is not scored on.
 * They are shown anyway, because a criterion nobody can see is a promise
 * nobody kept.
 */
function CriteriaDetail({ score }: { score: SessionScore }) {
  const lists = scoreLists(score);
  const groups = [
    ...lists.axes
      .filter((axis) => (axis.criteria ?? []).length > 0)
      .map((axis) => ({
        key: axis.axis,
        label: AXIS_LABELS[axis.axis] ?? axis.axis,
        criteria: axis.criteria,
      })),
    ...(lists.otherCriteria.length > 0
      ? [
          {
            key: "other",
            label: "Not scored on this purpose",
            criteria: lists.otherCriteria,
          },
        ]
      : []),
  ];

  if (groups.length === 0) {
    return (
      <p className="border-hairline border-t pt-3.5 text-ink-muted text-sm">
        This prescription froze no success criteria, so there is nothing under
        the axes to check.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1.5 border-hairline border-t pt-3.5">
      {groups.map((group) => (
        <details key={group.key} className="group/criteria">
          <summary className="cursor-pointer list-none text-ink-muted text-sm hover:text-ink">
            <span className="mr-1.5 inline-block font-mono text-ink-faint text-2xs group-open/criteria:rotate-90">
              ▸
            </span>
            {group.label} — {tally(group.criteria)}
          </summary>
          <ul className="mt-1.5 mb-1 flex flex-col gap-1 pl-4">
            {group.criteria.map((outcome) => (
              <li
                key={`${outcome.index}-${outcome.kind}`}
                className="flex items-baseline gap-2 text-sm"
              >
                <Outcome outcome={outcome} />
                <span className="text-ink-faint">
                  {CRITERION_LABELS[outcome.kind] ?? outcome.kind}
                </span>
                <span className="min-w-0 text-ink-secondary">
                  {outcome.detail}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}

/** `2 of 3 passed` — and never "1 failed" for a criterion nothing could check. */
function tally(criteria: readonly CriterionOutcome[]): string {
  const checked = criteria.filter((one) => one.passed !== null);
  const passed = checked.filter((one) => one.passed).length;
  const unchecked = criteria.length - checked.length;
  const head =
    checked.length === 0
      ? "none could be checked"
      : `${passed} of ${checked.length} passed`;
  return unchecked > 0 ? `${head}, ${unchecked} not assessed` : head;
}

/**
 * A criterion's mark: passed, failed, or **not assessed**.
 *
 * Three states, and the third is not a failure. A ride with no power meter did
 * not fail its time-in-band criterion, so it gets the placeholder with the
 * reason rather than a red cross.
 */
function Outcome({ outcome }: { outcome: CriterionOutcome }) {
  if (outcome.passed === null || outcome.passed === undefined) {
    return (
      <NotAssessed
        reason={outcome.not_assessed ?? "This criterion could not be checked."}
        symbol="?"
      />
    );
  }
  return (
    <span
      role="img"
      aria-label={outcome.passed ? "Passed" : "Failed"}
      className={cn(
        "font-mono",
        outcome.passed ? "text-status-completed" : "text-destructive",
      )}
    >
      {outcome.passed ? "✓" : "✕"}
    </span>
  );
}

// --- the verdict ----------------------------------------------------------

/**
 * The suggestion, the declaration, and the one control that turns one into the
 * other.
 *
 * Confirming is one tap **when the suggestion needs no reasons**. Anything but
 * `as_intended` requires one to three of them (WP-7.3), so confirming such a
 * suggestion opens the same form the override picker opens, with the verdict
 * already chosen — the tap that would have been a declaration becomes the tap
 * that starts one, rather than a button that silently fails at the server.
 */
function VerdictBlock({
  sessionId,
  timezone,
  score,
  declaration,
}: {
  sessionId: string;
  timezone: string;
  score: SessionScore;
  declaration: VerdictDeclaration | null;
}) {
  const [form, setForm] = useState<FormState | null>(null);
  const queryClient = useQueryClient();
  const path = { params: { path: { session_id: sessionId } } };

  // Both facets, and through `queryOptions` rather than a hand-built key: the
  // suggestion is on the score and the declaration is its own resource, and a
  // page showing yesterday's suggestion beside today's declaration would be
  // showing two judgements at once.
  const refresh = () => {
    setForm(null);
    for (const key of [
      $api.queryOptions("get", "/api/v1/sessions/{session_id}/score", path)
        .queryKey,
      $api.queryOptions("get", "/api/v1/sessions/{session_id}/verdict", path)
        .queryKey,
    ]) {
      queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const declare = $api.useMutation(
    "put",
    "/api/v1/sessions/{session_id}/verdict",
    { onSuccess: refresh },
  );
  const revise = $api.useMutation(
    "put",
    "/api/v1/sessions/{session_id}/verdict/reasons",
    { onSuccess: refresh },
  );
  const busy = declare.isPending || revise.isPending;
  const problems = [
    ...apiErrorMessages(declare.error),
    ...apiErrorMessages(revise.error),
  ];

  const open = (state: FormState) => setForm(state);

  const submit = (verdict: Verdict, reasons: Reason[], note: string | null) => {
    if (form?.mode === "reasons") {
      revise.mutate({
        ...path,
        body: { reasons, note, revision_reason: null },
      });
      return;
    }
    declare.mutate({ ...path, body: { verdict, reasons, note } });
  };

  const confirmSuggestion = () => {
    if (score.suggested_verdict === "as_intended") {
      declare.mutate({
        ...path,
        body: { verdict: "as_intended", reasons: [], note: null },
      });
      return;
    }
    open({ mode: "declare", verdict: score.suggested_verdict, reasons: [] });
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Only when there is one. A score that names no suggestion is a broken
          artefact, and the honest thing to do about it is to say nothing on
          the machine's behalf — the athlete can still declare. */}
      {score.suggested_verdict ? (
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <SectionLabel>arc suggests</SectionLabel>
          <VerdictBadge verdict={score.suggested_verdict} />
          <span className="max-w-[62ch] text-ink-secondary text-sm">
            {score.verdict_rationale}
          </span>
        </div>
      ) : null}

      {declaration ? (
        <Declared declaration={declaration} timezone={timezone} />
      ) : null}

      {declaration?.contested && declaration.contested_verdict ? (
        <ContestedBanner
          declaration={declaration}
          contested={declaration.contested_verdict}
          busy={busy}
          onReconfirm={() =>
            declare.mutate({
              ...path,
              body: {
                verdict: declaration.declared_verdict,
                reasons: declaration.reasons?.reasons ?? [],
                note: declaration.reasons?.note ?? null,
              },
            })
          }
        />
      ) : null}

      {form ? (
        <DeclareForm
          key={`${form.mode}-${form.verdict}`}
          initial={form}
          busy={busy}
          onCancel={() => setForm(null)}
          onSubmit={submit}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {declaration || !score.suggested_verdict ? null : (
            <Button disabled={busy} onClick={confirmSuggestion}>
              {`Confirm — ${verdictLabel(score.suggested_verdict).toLowerCase()}`}
            </Button>
          )}
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() =>
              open({
                mode: "declare",
                verdict: declaration?.declared_verdict ?? "as_intended",
                reasons: [...(declaration?.reasons?.reasons ?? [])],
                note: declaration?.reasons?.note ?? "",
              })
            }
          >
            {declaration ? "Change what you said" : "It was something else"}
          </Button>
          {declaration && declaration.declared_verdict !== "as_intended" ? (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() =>
                open({
                  mode: "reasons",
                  verdict: declaration.declared_verdict,
                  reasons: [...(declaration.reasons?.reasons ?? [])],
                  note: declaration.reasons?.note ?? "",
                })
              }
            >
              Revise the reasons
            </Button>
          ) : null}
        </div>
      )}

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
    </div>
  );
}

/** What the athlete said, when they said it, and why. */
function Declared({
  declaration,
  timezone,
}: {
  declaration: VerdictDeclaration;
  timezone: string;
}) {
  const stamp = localStamp(declaration.declared_at, timezone);
  const reasons = declaration.reasons?.reasons ?? [];
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      <SectionLabel>You said</SectionLabel>
      <VerdictBadge verdict={declaration.declared_verdict} />
      {reasons.length > 0 ? (
        // Ordered, because the order is data: the first reason is the main
        // one, and a revision that only reorders them is a real revision.
        <ol className="flex flex-wrap items-baseline gap-1.5">
          {reasons.map((reason, position) => (
            <li key={reason} className="text-ink-secondary text-sm">
              <span className="mr-1 font-mono text-ink-faint text-2xs">
                {position + 1}
              </span>
              {REASON_LABELS[reason]}
            </li>
          ))}
        </ol>
      ) : null}
      {declaration.reasons?.note ? (
        <span className="text-ink-muted text-sm">
          “{declaration.reasons.note}”
        </span>
      ) : null}
      {stamp ? (
        <span className="font-mono text-2xs text-ink-faint">
          {stamp.date} {stamp.time}
        </span>
      ) : null}
    </div>
  );
}

/**
 * A later score disagrees with what the athlete declared (WP-7.4).
 *
 * **Surfaced, never acted on.** The declaration stands exactly as it was
 * written; the banner puts the machine's new opinion beside it and offers the
 * one action that resolves the disagreement — re-declaring, which is the
 * athlete ruling on the current opinion and is what clears the flag server-side.
 */
function ContestedBanner({
  declaration,
  contested,
  busy,
  onReconfirm,
}: {
  declaration: VerdictDeclaration;
  contested: Verdict;
  busy: boolean;
  onReconfirm: () => void;
}) {
  return (
    <div
      role="status"
      data-testid="contested-banner"
      className="flex flex-wrap items-center gap-2.5 rounded-card border border-warn-border bg-warn-surface px-3.5 py-2.5"
    >
      <span className="mr-auto max-w-[62ch] text-ink-secondary text-sm">
        A later score says this was{" "}
        <strong className="text-ink">{verdictLabel(contested)}</strong>. You
        said{" "}
        <strong className="text-ink">
          {verdictLabel(declaration.declared_verdict)}
        </strong>
        , and that is what stands — nothing has changed. Re-confirm to say you
        have seen this.
      </span>
      <Button size="sm" disabled={busy} onClick={onReconfirm}>
        Re-confirm {verdictLabel(declaration.declared_verdict).toLowerCase()}
      </Button>
    </div>
  );
}

/** One verdict, coloured by the state it puts a session in. */
function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return (
    <span
      data-verdict={verdict}
      className="whitespace-nowrap rounded-badge border border-hairline px-1.5 py-0.5 font-medium text-ink text-sm"
    >
      {verdictLabel(verdict)}
    </span>
  );
}

// --- declaring ------------------------------------------------------------

interface FormState {
  /** `declare` writes the verdict and its reasons; `reasons` only revises. */
  readonly mode: "declare" | "reasons";
  readonly verdict: Verdict;
  readonly reasons: readonly Reason[];
  readonly note?: string;
}

/**
 * The override picker and the reason picker, in one surface.
 *
 * The reasons appear for any declaration that is not `as_intended`, because
 * that is exactly when the API requires them (WP-7.3) — so the form asks for
 * what the server will demand rather than discovering it in a 422. They are
 * **ordered by primacy**: clicking a reason appends it, so the list reads in
 * the order it was built and the first one is the main one.
 *
 * Closing it with edits in hand asks first (D82): the picker is a dismissible
 * editing surface like any other.
 */
function DeclareForm({
  initial,
  busy,
  onCancel,
  onSubmit,
}: {
  initial: FormState;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (verdict: Verdict, reasons: Reason[], note: string | null) => void;
}) {
  const base = useId();
  const [verdict, setVerdict] = useState<Verdict>(initial.verdict);
  const [reasons, setReasons] = useState<Reason[]>([...initial.reasons]);
  const [note, setNote] = useState(initial.note ?? "");

  const dirty =
    verdict !== initial.verdict ||
    reasons.join() !== initial.reasons.join() ||
    note !== (initial.note ?? "");
  const close = useDirtyClose({ dirty, onClose: onCancel });

  const needsReasons = verdict !== "as_intended";
  const full = reasons.length >= MAX_REASONS;
  const problem = reasonsProblem(verdict, reasons);

  const toggle = (reason: Reason) => {
    setReasons((held) =>
      held.includes(reason)
        ? held.filter((one) => one !== reason)
        : held.length >= MAX_REASONS
          ? held
          : [...held, reason],
    );
  };

  return (
    <form
      data-testid="declare-form"
      className="flex flex-col gap-3 rounded-card border border-hairline bg-inset px-3.5 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (problem) {
          return;
        }
        onSubmit(verdict, reasons, note.trim() === "" ? null : note.trim());
      }}
    >
      {initial.mode === "declare" ? (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1.5">
            <SectionLabel>What was it?</SectionLabel>
          </legend>
          {VERDICT_ORDER.map((one) => (
            <label
              key={one}
              className="flex cursor-pointer items-baseline gap-2 text-sm"
            >
              <input
                type="radio"
                name={`${base}-verdict`}
                value={one}
                checked={verdict === one}
                onChange={() => setVerdict(one)}
              />
              <span className="text-ink">{VERDICT_LABELS[one]}</span>
              <span className="text-ink-muted text-xs">
                {VERDICT_HINTS[one]}
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}

      {needsReasons ? (
        <fieldset className="flex flex-col gap-2 border-hairline border-t pt-3">
          <legend className="mb-1.5">
            <SectionLabel>
              Why — up to {MAX_REASONS}, most important first
            </SectionLabel>
          </legend>
          <div className="flex flex-wrap gap-1.5">
            {REASON_ORDER.map((reason) => {
              const position = reasons.indexOf(reason);
              const picked = position >= 0;
              return (
                <label
                  key={reason}
                  className={cn(
                    "flex cursor-pointer items-baseline gap-1 rounded-badge border px-1.5 py-0.5 text-sm",
                    picked
                      ? "border-accent-border bg-accent-surface text-accent"
                      : "border-hairline text-ink-muted",
                    !picked && full && "opacity-50",
                  )}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={picked}
                    disabled={!picked && full}
                    onChange={() => toggle(reason)}
                  />
                  {picked ? (
                    <span className="font-mono text-2xs">{position + 1}</span>
                  ) : null}
                  {REASON_LABELS[reason]}
                </label>
              );
            })}
          </div>
          {full ? (
            <p className="text-ink-faint text-xs">
              Three is the most a declaration carries. Deselect one to pick
              another.
            </p>
          ) : null}
          <label htmlFor={`${base}-note`} className="text-ink-muted text-xs">
            In your own words (optional)
          </label>
          <Textarea
            id={`${base}-note`}
            value={note}
            maxLength={MAX_REASON_NOTE_CHARS}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Beside the reasons, never instead of them."
          />
        </fieldset>
      ) : null}

      {problem ? (
        <p role="alert" className="text-destructive text-sm">
          {problem}
        </p>
      ) : null}

      {close.confirming ? (
        <DiscardPrompt
          what="what you were about to say about this session"
          onDiscard={close.discard}
          onKeepEditing={close.keepEditing}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={busy || problem !== null}>
            {initial.mode === "reasons" ? "Save the reasons" : "Save"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={close.requestClose}
          >
            Cancel
          </Button>
        </div>
      )}
    </form>
  );
}
