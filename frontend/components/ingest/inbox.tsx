"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useId, useRef, useState } from "react";

import { ConfirmButton, InlineConfirm } from "@/components/design/confirm";
import { Td, Th } from "@/components/design/data-table";
import { Pager } from "@/components/design/pager";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import type { components, paths } from "@/generated/api/schema";
import { SESSIONS_QUERY_PREFIX } from "@/lib/activity";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";
import { formatUtcStamp } from "@/lib/format";
import {
  describeReport,
  INGEST_EVENTS_QUERY_PREFIX,
  INGEST_OUTCOMES,
  type IngestOutcome,
  type IngestReport,
  QUARANTINE_QUERY_PREFIX,
  QUARANTINE_REASONS,
  QUARANTINE_STATUSES,
  type QuarantineRecord,
  REJECT_OFFERS,
  waitingLabel,
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
 * that was refused is a 200 carrying `outcome: "quarantined"`, and a
 * client reading the status instead would report success and show nothing.
 */
export function Inbox() {
  const [queueOffset, setQueueOffset] = useState(0);
  const [eventOffset, setEventOffset] = useState(0);

  const quarantine = $api.useQuery("get", "/api/v1/ingest/quarantine", {
    params: { query: { offset: queueOffset, limit: QUARANTINE_PAGE } },
  });
  const events = $api.useQuery("get", "/api/v1/ingest/events", {
    params: { query: { offset: eventOffset, limit: EVENTS_PAGE } },
  });

  const records = quarantine.data?.items ?? [];
  const queueTotal = quarantine.data?.total ?? 0;
  const pending = records.filter((record) => record.status === "pending");
  const resolved = records.filter((record) => record.status !== "pending");

  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Inbox</h1>
        <span className="font-mono text-ink-muted text-sm">
          {quarantine.data
            ? waitingLabel({
                pending: pending.length,
                onPage: records.length,
                offset: queueOffset,
                total: queueTotal,
              })
            : ""}
        </span>
      </Toolbar>

      <PageBody className="flex flex-col gap-5">
        <UploadPanel />

        <section className="flex flex-col gap-2.5">
          {/* One pager for the whole queue, not one per band: the endpoint
              returns pending first and resolved after (`list_quarantine`), so
              a page is a slice of that single order and the two bands below
              are how it is *read*, not what it is paged by. The range counts
              records, therefore, and the toolbar counts what is waiting. */}
          <Pager
            heading="Waiting on you"
            subject="quarantine records"
            offset={queueOffset}
            onPage={records.length}
            total={queueTotal}
            pageSize={QUARANTINE_PAGE}
            onOffsetChange={setQueueOffset}
          />
          {quarantine.isPending ? (
            <p className="text-ink-muted text-sm">Loading the queue…</p>
          ) : quarantine.error ? (
            <p role="alert" className="text-destructive text-sm">
              {loadFailureMessage(quarantine.error, "the queue")}
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
          error={events.error}
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
      {/* One link per session, and each one says *which*. A multisport file
          is ingested as one session per sport (A4.5), and N links all reading
          "Open the session" are N controls a screen reader announces
          identically and a person has to click through to tell apart. The
          report carries ids and nothing to name them by, so the label counts:
          the order here is the order the sports were in the file. */}
      {report.session_ids.map((id, index) => (
        <Link
          key={id}
          href={`/sessions/${id}`}
          className="text-accent underline-offset-2 hover:underline"
        >
          {report.session_ids.length === 1
            ? "Open the session"
            : `Open session ${index + 1}`}
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
  // What overruling *this* verdict is called, or nothing where the API would
  // answer 409. Undefined is the whole condition for the second button.
  const offer = REJECT_OFFERS[record.reason];
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
          {/* The event table below heads its column `At · UTC`; a card has
              no column to head, so the zone rides on the stamp. Bare, an
              08:00 quarantine reads as 18:00 the previous day for an athlete
              at UTC+14 (`formatUtcStamp`). */}
          <span className="font-mono text-2xs text-ink-faint">
            {formatUtcStamp(record.created_at)} UTC
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
          <span>resolved {formatUtcStamp(record.resolved_at)} UTC</span>
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
          {offer && !rejecting ? (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => setRejecting(true)}
            >
              {offer.label}
            </Button>
          ) : null}
        </div>
      ) : null}

      {rejecting && offer ? (
        <InlineConfirm
          question={offer.question}
          confirmLabel={offer.confirmLabel}
          cancelLabel="Keep waiting"
          // Disabled while the first answer is in flight: a second click is
          // the same decision twice, and its 409 would land *after* the
          // success and paint a refusal over a reject that went through.
          disabled={busy}
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
  error,
  onOffsetChange,
}: {
  items: readonly IngestEvent[];
  total: number;
  offset: number;
  loading: boolean;
  /** Whatever the query threw, or null. Carries its own status (D-note in
      `lib/api-errors.ts`), so the message can name the right remedy. */
  error: unknown;
  onOffsetChange: (offset: number) => void;
}) {
  return (
    <section className="flex flex-col gap-2.5">
      <Pager
        heading="Ingest log"
        subject="ingest log rows"
        offset={offset}
        onPage={items.length}
        total={total}
        pageSize={EVENTS_PAGE}
        onOffsetChange={onOffsetChange}
      />

      {loading ? (
        <p className="text-ink-muted text-sm">Loading the log…</p>
      ) : error != null ? (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(error, "the ingest log")}
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
