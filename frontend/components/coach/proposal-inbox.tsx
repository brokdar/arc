"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ProposalDiff } from "@/components/coach/proposal-diff";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { SESSIONS_QUERY_PREFIX } from "@/lib/activity";
import { $api } from "@/lib/api/client";
import {
  apiErrorMessages,
  isConflict,
  loadFailureMessage,
} from "@/lib/api-errors";
import { formatUtcStamp } from "@/lib/format";
import {
  MATCH_QUERY_PREFIX,
  MATCHES_QUERY_PREFIX,
  PLAN_WEEK_QUERY_PREFIX,
  PLANNED_SESSION_QUERY_PREFIX,
  PLANNED_SESSIONS_QUERY_PREFIX,
} from "@/lib/matching";
import {
  actorLabel,
  expiryLabel,
  isActionable,
  PROPOSAL_STATUS_LABELS,
  PROPOSAL_STATUS_TONES,
  PROPOSAL_STATUSES,
  PROPOSALS_QUERY_PREFIX,
  type Proposal,
  type ProposalStatus,
} from "@/lib/proposals";
import { cn } from "@/lib/utils";

/** How many proposals one page of the inbox holds. */
const PAGE = 25;

/**
 * The proposal inbox: everything the coach has suggested, and the two answers.
 *
 * Filtered to `pending` on open, because that is what an inbox is — the rest
 * are outcomes, kept because "what did I say no to, and why" is the question
 * this page is opened for once the queue is empty. The filter is client state
 * rather than a query parameter of the URL (UI convention 1): a filtered
 * inbox is not a place worth bookmarking, and the endpoint takes the status
 * itself so the filtering happens on the server either way.
 *
 * Nothing on this page can edit a proposal. The athlete's whole vocabulary is
 * accept, reject-with-a-reason, and let it lapse — the agent proposes, the
 * committed plan stands by default, and a proposal that could be amended
 * before it was applied would be a plan edit wearing a proposal's clothes.
 */
export function ProposalInbox() {
  const filterId = useId();
  const [status, setStatus] = useState<ProposalStatus | "">("pending");
  const [offset, setOffset] = useState(0);

  const proposals = $api.useQuery("get", "/api/v1/proposals", {
    params: {
      query: { ...(status === "" ? {} : { status }), offset, limit: PAGE },
    },
  });

  const items = proposals.data?.items ?? [];
  const total = proposals.data?.total ?? 0;
  const last = Math.min(offset + items.length, total);

  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Proposals</h1>
        <span className="font-mono text-ink-muted text-sm">
          {proposals.data ? `${total} ${statusWord(status)}` : ""}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <label htmlFor={filterId} className="text-ink-muted text-xs">
            Show
          </label>
          <NativeSelect
            id={filterId}
            size="sm"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as ProposalStatus | "");
              setOffset(0);
            }}
          >
            <NativeSelectOption value="">All</NativeSelectOption>
            {PROPOSAL_STATUSES.map((value) => (
              <NativeSelectOption key={value} value={value}>
                {PROPOSAL_STATUS_LABELS[value]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
      </Toolbar>

      <PageBody className="flex flex-col gap-3.5">
        <p className="max-w-[72ch] text-ink-muted text-base">
          A coaching agent proposes changes to the plan; nothing it suggests
          takes effect until you accept it. A proposal you never answer lapses
          at its expiry and the committed plan stands.
        </p>

        {total > PAGE ? (
          <div className="flex items-baseline gap-2.5">
            <SectionLabel level={2}>
              {PROPOSAL_STATUS_LABELS[status === "" ? "pending" : status]}
            </SectionLabel>
            <span className="font-mono text-2xs text-ink-faint">
              {`${offset + 1}–${last} of ${total}`}
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              <Button
                size="xs"
                variant="secondary"
                aria-label="Newer proposals"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                Newer
              </Button>
              <Button
                size="xs"
                variant="secondary"
                aria-label="Older proposals"
                disabled={last >= total}
                onClick={() => setOffset(offset + PAGE)}
              >
                Older
              </Button>
            </span>
          </div>
        ) : null}

        {proposals.isPending ? (
          <p className="text-ink-muted text-sm">Loading the proposals…</p>
        ) : proposals.error ? (
          <p role="alert" className="text-destructive text-sm">
            {loadFailureMessage(proposals.error, "the proposals")}
          </p>
        ) : items.length === 0 ? (
          <EmptyInbox status={status} />
        ) : (
          <ul className="flex flex-col gap-3">
            {items.map((proposal) => (
              <li key={proposal.id}>
                <ProposalCard proposal={proposal} />
              </li>
            ))}
          </ul>
        )}
      </PageBody>
    </>
  );
}

/** The noun the toolbar counts, so "0 waiting" is not "0 proposals". */
function statusWord(status: ProposalStatus | ""): string {
  if (status === "") {
    return "proposals";
  }
  return status === "pending"
    ? "waiting on you"
    : PROPOSAL_STATUS_LABELS[status].toLowerCase();
}

/**
 * An empty queue, with the reason it might be empty.
 *
 * The remedy an empty state is supposed to name (UI convention 3) is not a
 * control on this page: proposals arrive over MCP, so the action that fills
 * this inbox is connecting a coaching agent to the server. Naming that is the
 * honest version of the rule — the alternative, "No proposals yet", is the
 * dead end the rule exists to forbid.
 */
function EmptyInbox({ status }: { status: ProposalStatus | "" }) {
  return (
    <Panel className="flex flex-col gap-1.5 px-5 py-4">
      <p className="text-base text-ink-secondary">
        {status === "pending"
          ? "Nothing is waiting on you."
          : `No proposal is ${PROPOSAL_STATUS_LABELS[status === "" ? "pending" : status].toLowerCase()}.`}
      </p>
      <p className="max-w-[68ch] text-ink-muted text-sm">
        Proposals are written by a coaching agent connected to arc's MCP server
        (see <code className="font-mono">docs/agent-setup.md</code>). Until one
        is connected and has something to suggest, this queue stays empty and
        the plan is entirely yours.
      </p>
    </Panel>
  );
}

/**
 * One proposal: why, what, and by when.
 *
 * The rationale is drawn in the coach's purple and the diff is not. That split
 * is invariant 7 made visible on one card: the sentence explaining the change
 * was written by a language model and the change itself is a computed diff
 * over the committed plan, and an athlete deciding whether to accept needs to
 * know which half is which.
 */
function ProposalCard({ proposal }: { proposal: Proposal }) {
  const reasonId = useId();
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: PROPOSALS_QUERY_PREFIX });

  const accept = $api.useMutation(
    "post",
    "/api/v1/proposals/{proposal_id}/accept",
    {
      onSuccess: () => {
        // The plan moved: every week the accepted changes touch is stale, and
        // so is the inbox that still lists this one as pending. The two
        // planned-session keys are both here because they are separate caches
        // and react-query matches a key element for element — the calendar
        // reads the list, and Today's panel and the session sheet each read
        // one session by id, so a week-only invalidation leaves the athlete
        // looking at the prescription the accept just replaced.
        queryClient.invalidateQueries({ queryKey: PLAN_WEEK_QUERY_PREFIX });
        queryClient.invalidateQueries({
          queryKey: PLANNED_SESSIONS_QUERY_PREFIX,
        });
        queryClient.invalidateQueries({
          queryKey: PLANNED_SESSION_QUERY_PREFIX,
        });
        // Accepting a delete cascades the planned session's `session_matches`
        // and nulls the scores that hung off them (the same change class the
        // match panel invalidates for), so an open session or match view goes
        // on drawing a link to a planned session the accept just removed unless
        // these three caches are dropped too (FIX-F5).
        queryClient.invalidateQueries({ queryKey: MATCHES_QUERY_PREFIX });
        queryClient.invalidateQueries({ queryKey: MATCH_QUERY_PREFIX });
        queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_PREFIX });
        invalidate();
      },
    },
  );
  const reject = $api.useMutation(
    "post",
    "/api/v1/proposals/{proposal_id}/reject",
    {
      onSuccess: () => {
        setRejecting(false);
        setReason("");
        invalidate();
      },
    },
  );

  const busy = accept.isPending || reject.isPending;
  const path = { params: { path: { proposal_id: proposal.id } } };
  // Not merely `pending`: an expired proposal reads pending until the sweep
  // catches it, and offering Accept on one only earns a 409 (FIX-F4).
  const actionable = isActionable(proposal);
  const conflict = isConflict(accept.error);
  // A 409 is a state this card draws, not a message it prints — so it is kept
  // out of the generic problem list and rendered on its own below.
  const problems = [
    ...(conflict ? [] : apiErrorMessages(accept.error)),
    ...apiErrorMessages(reject.error),
  ];

  return (
    <Panel
      tone="card"
      data-testid="proposal"
      data-status={proposal.status}
      className="flex flex-col gap-3 px-4 py-3.5"
    >
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span
          className={cn(
            "rounded-badge border px-1.5 py-0.5 text-2xs",
            PROPOSAL_STATUS_TONES[proposal.status],
          )}
        >
          {PROPOSAL_STATUS_LABELS[proposal.status]}
        </span>
        <span className="font-mono text-ink-secondary text-sm">
          {actorLabel(proposal.created_by)}
        </span>
        <span className="font-mono text-2xs text-ink-faint">
          {formatUtcStamp(proposal.created_at)}
        </span>
        <span className="ml-auto font-mono text-2xs text-ink-faint">
          {/* The expiry is shown on every proposal, not only the pending ones:
              on a lapsed one it is the reason it lapsed. */}
          {expiryLabel(proposal.expires_at)} ·{" "}
          {formatUtcStamp(proposal.expires_at)}
        </span>
      </div>

      <p className="max-w-[76ch] rounded-button border border-coach-border bg-coach-surface px-3.5 py-2.5 text-base text-coach-ink leading-relaxed">
        {proposal.rationale}
      </p>

      <ProposalDiff changes={proposal.diff} />

      {proposal.supersedes_id || proposal.superseded_by_id ? (
        <p className="font-mono text-2xs text-ink-faint">
          {proposal.supersedes_id
            ? `supersedes ${proposal.supersedes_id.slice(0, 8)}`
            : ""}
          {proposal.supersedes_id && proposal.superseded_by_id ? " · " : ""}
          {proposal.superseded_by_id
            ? `superseded by ${proposal.superseded_by_id.slice(0, 8)}`
            : ""}
        </p>
      ) : null}

      {proposal.resolution_note ? (
        <p className="text-ink-muted text-sm">
          <span className="text-ink-faint">Resolution: </span>
          {proposal.resolution_note}
        </p>
      ) : null}

      {conflict ? (
        <ConflictNotice message={apiErrorMessages(accept.error)[0]} />
      ) : null}

      {actionable ? (
        <div className="flex flex-wrap items-center gap-2 border-hairline border-t pt-3">
          <Button size="sm" disabled={busy} onClick={() => accept.mutate(path)}>
            {accept.isPending ? "Applying…" : "Accept"}
          </Button>
          {rejecting ? null : (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => setRejecting(true)}
            >
              Reject
            </Button>
          )}
          <span className="ml-auto text-2xs text-ink-faint">
            Do nothing and it lapses — the plan you have stands.
          </span>
        </div>
      ) : null}

      {rejecting ? (
        <div className="flex flex-col gap-2 rounded-button border border-hairline-strong bg-inset px-3.5 py-3">
          <label htmlFor={reasonId} className="text-ink-muted text-xs">
            Why not? — the coach reads this, and it is how the next proposal
            gets better. Optional.
          </label>
          <Textarea
            id={reasonId}
            rows={2}
            value={reason}
            placeholder="Too much on top of Saturday's ride."
            onChange={(event) => setReason(event.target.value)}
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setRejecting(false);
                setReason("");
              }}
            >
              Keep it waiting
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={busy}
              onClick={() =>
                reject.mutate({
                  ...path,
                  body: { reason: reason.trim() === "" ? null : reason.trim() },
                })
              }
            >
              {reject.isPending ? "Rejecting…" : "Reject it"}
            </Button>
          </div>
        </div>
      ) : null}

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
    </Panel>
  );
}

/**
 * The accept the server refused with a 409 — and *why*, in its own words.
 *
 * Deliberately not phrased as a failure, because nothing went wrong on the
 * athlete's side; the world moved between writing the proposal and accepting
 * it. But the three ways it can move are different facts and must not read
 * alike (FIX-F4): the plan was revised under the proposal (a stale intent
 * version), the proposal expired before it was answered, or it is no longer
 * pending at all. The server names which one in the message it sends, and each
 * message carries its own remedy — "re-read the session and propose again",
 * "the committed plan stands" — so the card prints that sentence rather than a
 * single house phrase that would be wrong for two cases out of three. All that
 * is added is the one reassurance every case shares: nothing was applied.
 */
function ConflictNotice({ message }: { message?: string }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-1 rounded-card border border-warn-border bg-warn-surface px-3.5 py-2.5"
    >
      <span className="font-medium text-sm text-status-under">
        {message ?? "This proposal could not be accepted."}
      </span>
      <span className="text-ink-muted text-xs">Nothing was applied.</span>
    </div>
  );
}
