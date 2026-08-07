"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId, useRef, useState } from "react";

import { ConfirmButton, InlineConfirm } from "@/components/design/confirm";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import type { components, paths } from "@/generated/api/schema";
import { SESSIONS_QUERY_PREFIX } from "@/lib/activity";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { formatUtcStamp } from "@/lib/format";
import {
  canReject,
  describeReport,
  INGEST_EVENTS_QUERY_PREFIX,
  INGEST_OUTCOMES,
  type IngestOutcome,
  type IngestReport,
  QUARANTINE_QUERY_PREFIX,
  QUARANTINE_REASONS,
  QUARANTINE_STATUSES,
  type QuarantineRecord,
} from "@/lib/ingest";

type IngestEvent = components["schemas"]["IngestEventRead"];

/** How many quarantine records one page of the queue holds. */
const QUARANTINE_PAGE = 50;
/** How many log lines one page of the ingest log holds. */
const EVENTS_PAGE = 20;

/**
 * The inbox: everything the watched folder could not decide on its own.
 *
 * Three bands, in the order they demand attention. The **upload panel** names
 * the two ways a file gets in — the folder and this control — and stays at the
 * top whether or not anything is waiting, so its position never moves under
 * the cursor (UI convention 3 and 4 together: an empty queue is not a dead
 * end, and the remedy does not migrate around the page). Then the **queue**,
 * pending first, because what is waiting on the athlete outranks what they
 * have already dealt with. Then the **log**, which is what the page is opened
 * for when nothing is waiting: "did it see my ride at all?".
 *
 * Nothing here branches on an HTTP status to decide what happened. An upload
 * that was refused is a 200 carrying `outcome: "quarantined"` (D97), and a
 * client reading the status instead would report success and show nothing.
 */
export function Inbox() {
  const [eventOffset, setEventOffset] = useState(0);

  const quarantine = $api.useQuery("get", "/api/v1/ingest/quarantine", {
    params: { query: { limit: QUARANTINE_PAGE } },
  });
  const events = $api.useQuery("get", "/api/v1/ingest/events", {
    params: { query: { offset: eventOffset, limit: EVENTS_PAGE } },
  });

  const records = quarantine.data?.items ?? [];
  const pending = records.filter((record) => record.status === "pending");
  const resolved = records.filter((record) => record.status !== "pending");

  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Inbox</h1>
        <span className="font-mono text-ink-muted text-sm">
          {quarantine.data
            ? pending.length === 0
              ? "nothing waiting"
              : `${pending.length} waiting`
            : ""}
        </span>
      </Toolbar>

      <PageBody className="flex flex-col gap-5">
        <UploadPanel />

        <section className="flex flex-col gap-2.5">
          <SectionLabel level={2}>Waiting on you</SectionLabel>
          {quarantine.isPending ? (
            <p className="text-ink-muted text-sm">Loading the queue…</p>
          ) : quarantine.error ? (
            <p role="alert" className="text-destructive text-sm">
              Could not load the queue. Is the API reachable?
            </p>
          ) : pending.length === 0 ? (
            <Panel className="px-5 py-4 text-ink-muted text-base">
              Nothing is waiting on you. Every file the pipeline has seen was
              either ingested or already known.
            </Panel>
          ) : (
            <ul className="flex flex-col gap-2">
              {pending.map((record) => (
                <li key={record.id}>
                  <QuarantineCard record={record} />
                </li>
              ))}
            </ul>
          )}
        </section>

        {resolved.length > 0 ? (
          <section className="flex flex-col gap-2.5">
            <SectionLabel level={2}>Already decided</SectionLabel>
            <ul className="flex flex-col gap-2">
              {resolved.map((record) => (
                <li key={record.id}>
                  <QuarantineCard record={record} />
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <IngestLog
          items={events.data?.items ?? []}
          total={events.data?.total ?? 0}
          offset={eventOffset}
          loading={events.isPending}
          failed={events.error != null}
          onOffsetChange={setEventOffset}
        />
      </PageBody>
    </>
  );
}

/**
 * The multipart body of an upload, typed as the generated schema spells it.
 *
 * openapi-typescript renders a binary part as `string`, and openapi-fetch's
 * default serializer passes a `FormData` body straight through so the browser
 * can set the boundary. The cast is where those two true facts meet, and it
 * lives here rather than at four call sites.
 */
type UploadBody =
  paths["/api/v1/ingest/upload"]["post"]["requestBody"]["content"]["multipart/form-data"];

function uploadBody(file: File): UploadBody {
  const form = new FormData();
  form.append("file", file);
  return form as unknown as UploadBody;
}

/**
 * The two ways a file gets into arc, and what the last one did.
 *
 * Named as an empty state even when the queue is full, because it is the
 * answer to "how do I get a ride in here?" either way — the folder is the
 * normal path and this control is the one for a file that is already on the
 * laptop.
 */
function UploadPanel() {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<IngestReport | null>(null);
  const queryClient = useQueryClient();

  const upload = $api.useMutation("post", "/api/v1/ingest/upload", {
    onSuccess: (result) => {
      setReport(result);
      setFile(null);
      if (input.current) {
        input.current.value = "";
      }
      queryClient.invalidateQueries({ queryKey: QUARANTINE_QUERY_PREFIX });
      queryClient.invalidateQueries({ queryKey: INGEST_EVENTS_QUERY_PREFIX });
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_PREFIX });
    },
  });

  const problems = upload.error ? apiErrorMessages(upload.error) : [];

  return (
    <Panel className="flex flex-col gap-3 px-5 py-4">
      <SectionLabel level={2}>Getting a ride in</SectionLabel>
      <p className="max-w-[62ch] text-ink-muted text-base">
        Drop FIT, TCX or GPX files into the inbox folder and they are ingested
        within the minute — or upload one here.
      </p>

      <form
        className="flex flex-wrap items-end gap-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          if (file) {
            setReport(null);
            upload.mutate({ body: uploadBody(file) });
          }
        }}
      >
        <div className="flex min-w-[240px] flex-1 flex-col gap-1">
          <label htmlFor={inputId} className="text-ink-muted text-xs">
            Activity file
          </label>
          <input
            id={inputId}
            ref={input}
            type="file"
            accept=".fit,.gpx,.tcx"
            className="h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-ink-secondary text-sm file:mr-2.5 file:rounded-badge file:border-0 file:bg-raised file:px-2 file:py-0.5 file:font-medium file:text-ink-secondary file:text-sm hover:file:bg-raised-hover"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setReport(null);
            }}
          />
        </div>
        <Button type="submit" disabled={file === null || upload.isPending}>
          {upload.isPending ? "Uploading…" : "Upload"}
        </Button>
      </form>

      {report ? <UploadOutcome report={report} /> : null}

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

/** What the pipeline did with the file that was just uploaded. */
function UploadOutcome({ report }: { report: IngestReport }) {
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-2.5 rounded-card border border-hairline-card bg-inset px-3.5 py-2.5 text-base"
    >
      <OutcomeMark outcome={report.outcome} />
      <span className="text-ink-secondary">{describeReport(report)}</span>
      {report.detail ? (
        <span className="text-ink-muted text-sm">{report.detail}</span>
      ) : null}
      {report.session_ids.map((id) => (
        <Link
          key={id}
          href={`/sessions/${id}`}
          className="text-accent underline-offset-2 hover:underline"
        >
          Open the session
        </Link>
      ))}
    </div>
  );
}

/**
 * One quarantined file: what it was, why it stopped here, and the two answers.
 *
 * Each card owns its own mutations so a refusal lands on the record it was
 * about. One mutation shared by the whole list would print "already resolved"
 * under every row at once — which is the shape of a lie, since only one of
 * them was refused.
 */
function QuarantineCard({ record }: { record: QuarantineRecord }) {
  const [rejecting, setRejecting] = useState(false);
  const queryClient = useQueryClient();
  const copy = QUARANTINE_REASONS[record.reason];
  const pending = record.status === "pending";

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: QUARANTINE_QUERY_PREFIX });
    queryClient.invalidateQueries({ queryKey: INGEST_EVENTS_QUERY_PREFIX });
    queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_PREFIX });
  };

  const confirm = $api.useMutation(
    "post",
    "/api/v1/ingest/quarantine/{record_id}/confirm",
    { onSuccess: invalidate },
  );
  const reject = $api.useMutation(
    "post",
    "/api/v1/ingest/quarantine/{record_id}/reject",
    {
      onSuccess: () => {
        setRejecting(false);
        invalidate();
      },
    },
  );

  const problems = [
    ...apiErrorMessages(confirm.error),
    ...apiErrorMessages(reject.error),
  ];
  const busy = confirm.isPending || reject.isPending;
  const variables = { params: { path: { record_id: record.id } } };

  return (
    <Panel
      tone="card"
      data-testid="quarantine-record"
      className="flex flex-col gap-2.5 px-4 py-3.5"
    >
      <div className="flex flex-wrap items-baseline gap-2.5">
        <h3 className="font-medium text-base text-ink">{copy.title}</h3>
        <span className="font-mono text-ink-secondary text-sm">
          {record.original_filename}
        </span>
        <span className="ml-auto flex items-center gap-2">
          <StatusPill status={record.status} />
          <span className="font-mono text-2xs text-ink-faint">
            {formatUtcStamp(record.created_at)}
          </span>
        </span>
      </div>

      {record.detail ? (
        <p className="text-ink-secondary text-sm">{record.detail}</p>
      ) : null}
      <p className="max-w-[72ch] text-ink-muted text-sm">{copy.remedy}</p>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-2xs text-ink-faint">
        <span title={`sha256 ${record.file_hash}`}>
          {record.file_hash.slice(0, 12)}
        </span>
        {record.file_sport_index !== null ? (
          <span>sport {record.file_sport_index}</span>
        ) : null}
        {record.resolved_at ? (
          <span>resolved {formatUtcStamp(record.resolved_at)}</span>
        ) : null}
        {record.suspected_session_id ? (
          <Link
            href={`/sessions/${record.suspected_session_id}`}
            className="font-sans text-accent text-sm underline-offset-2 hover:underline"
          >
            The session it looks like
          </Link>
        ) : null}
      </div>

      {pending ? (
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <ConfirmButton
            label="Discard this copy"
            question="Discard it?"
            confirmLabel="Discard"
            disabled={busy}
            onConfirm={() => confirm.mutate(variables)}
          />
          {canReject(record.reason) && !rejecting ? (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => setRejecting(true)}
            >
              Not a duplicate
            </Button>
          ) : null}
        </div>
      ) : null}

      {rejecting ? (
        <InlineConfirm
          question="Ingest this file as its own session?"
          confirmLabel="Ingest it"
          cancelLabel="Keep waiting"
          onConfirm={() => reject.mutate(variables)}
          onCancel={() => setRejecting(false)}
        />
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

/** Where a quarantined file stands. */
function StatusPill({ status }: { status: QuarantineRecord["status"] }) {
  return (
    <span
      className={
        status === "pending"
          ? "rounded-badge border border-warn-border bg-warn-surface px-1.5 py-0.5 text-2xs text-status-under"
          : "rounded-badge border border-hairline px-1.5 py-0.5 text-2xs text-ink-muted"
      }
    >
      {QUARANTINE_STATUSES[status]}
    </span>
  );
}

/** Every file the pipeline has looked at, newest first. */
function IngestLog({
  items,
  total,
  offset,
  loading,
  failed,
  onOffsetChange,
}: {
  items: readonly IngestEvent[];
  total: number;
  offset: number;
  loading: boolean;
  failed: boolean;
  onOffsetChange: (offset: number) => void;
}) {
  const last = Math.min(offset + items.length, total);
  return (
    <section className="flex flex-col gap-2.5">
      <div className="flex items-baseline gap-2.5">
        <SectionLabel level={2}>Ingest log</SectionLabel>
        <span className="font-mono text-2xs text-ink-faint">
          {total === 0 ? "" : `${offset + 1}–${last} of ${total}`}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <Button
            size="xs"
            variant="secondary"
            disabled={offset === 0}
            onClick={() => onOffsetChange(Math.max(0, offset - EVENTS_PAGE))}
          >
            Newer
          </Button>
          <Button
            size="xs"
            variant="secondary"
            disabled={last >= total}
            onClick={() => onOffsetChange(offset + EVENTS_PAGE)}
          >
            Older
          </Button>
        </span>
      </div>

      {loading ? (
        <p className="text-ink-muted text-sm">Loading the log…</p>
      ) : failed ? (
        <p role="alert" className="text-destructive text-sm">
          Could not load the ingest log.
        </p>
      ) : items.length === 0 ? (
        <Panel className="px-5 py-4 text-ink-muted text-base">
          The pipeline has not seen a file yet. Drop one into the inbox folder
          or upload it above, and every attempt lands here.
        </Panel>
      ) : (
        <Panel className="overflow-hidden">
          <table className="w-full border-collapse text-base">
            <thead>
              <tr className="border-hairline border-b text-left">
                <Th className="w-[104px]">At · UTC</Th>
                <Th className="w-[120px]">Outcome</Th>
                <Th>File</Th>
                <Th>Detail</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((event) => (
                <tr
                  key={event.id}
                  className="border-hairline-faint border-b last:border-b-0"
                >
                  <Td className="font-mono text-ink-faint text-sm">
                    {formatUtcStamp(event.at)}
                  </Td>
                  <Td>
                    <span className="flex items-center gap-1.5">
                      <OutcomeMark outcome={event.outcome} />
                      <span className="text-ink-secondary text-sm">
                        {INGEST_OUTCOMES[event.outcome]}
                      </span>
                    </span>
                  </Td>
                  <Td className="font-mono text-ink-secondary text-sm">
                    {event.session_id ? (
                      <Link
                        href={`/sessions/${event.session_id}`}
                        className="text-accent underline-offset-2 hover:underline"
                      >
                        {event.filename}
                      </Link>
                    ) : (
                      event.filename
                    )}
                  </Td>
                  <Td className="text-ink-muted text-sm">
                    {event.detail ?? ""}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </section>
  );
}

function Th({ className, children }: { className?: string; children: string }) {
  return (
    <th
      scope="col"
      className={`px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em] ${className ?? ""}`}
    >
      {children}
    </th>
  );
}

function Td({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <td className={`px-3.5 py-2 align-top ${className ?? ""}`}>{children}</td>
  );
}

/**
 * The coloured dot beside an outcome.
 *
 * Labelled, never decorative: colour is a second channel on top of the word
 * beside it, the same rule `StatusDot` follows for a session's status.
 */
const OUTCOME_INK: Readonly<Record<IngestOutcome, string>> = {
  ingested: "bg-status-completed",
  duplicate_file: "bg-status-pending",
  quarantined: "bg-status-under",
  error: "bg-status-missed",
};

function OutcomeMark({ outcome }: { outcome: IngestOutcome }) {
  return (
    <span
      role="img"
      aria-label={INGEST_OUTCOMES[outcome]}
      className={`size-1.5 shrink-0 rounded-full ${OUTCOME_INK[outcome]}`}
    />
  );
}
