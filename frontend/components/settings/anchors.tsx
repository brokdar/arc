"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ProvenanceMark } from "@/components/design/anchor-provenance";
import { Td, Th } from "@/components/design/data-table";
import { Field, FieldRow } from "@/components/design/field";
import { NotAssessed } from "@/components/design/not-assessed";
import { Pager } from "@/components/design/pager";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  ANCHOR_COPY,
  ANCHORS_QUERY_PREFIX,
  type AnchorType,
  type AnchorVersion,
  CURRENT_ANCHOR_QUERY_PREFIX,
  PROVENANCE_OPTIONS,
  type Provenance,
  WRITABLE_ANCHOR_TYPES,
  type WritableAnchorType,
  ZONES_QUERY_PREFIX,
} from "@/lib/anchors";
import { $api } from "@/lib/api/client";
import {
  apiErrorMessages,
  isNotFound,
  loadFailureMessage,
} from "@/lib/api-errors";
import { todayIsoDate } from "@/lib/dates";
import {
  formatAnchorValue,
  formatDayMonthYear,
  formatUtcStamp,
  parseNumberInput,
} from "@/lib/format";
import { anchorLabel } from "@/lib/targets";

/** How many versions one page of the history holds. */
const HISTORY_PAGE = 20;

/**
 * The sentence that is on the page whether or not the athlete goes looking for
 * the rule. There is no edit and no delete anywhere in this component, and the
 * API answers PUT, PATCH and DELETE on a version with a 405 saying the same
 * thing (`app.api.routes.anchors.APPEND_ONLY_DETAIL`).
 */
const APPEND_ONLY_COPY =
  "Anchors are append-only. A wrong value is corrected by appending a new " +
  "version — the old one stays in the history, so every session, zone and " +
  "score that was resolved against it still explains itself.";

/**
 * The four anchors in force, in fixed slots.
 *
 * One query per anchor rather than a fold over the history: "in force" is a
 * domain rule — effective on or before today, appended on or before now,
 * latest of those (`app.domain.anchors.anchor_as_of`) — and a client that
 * re-derived it would be a second implementation free to disagree with the
 * one every resolved watt on every other screen came from.
 *
 * A missing anchor keeps its slot and says what it costs (UI conventions 3
 * and 4): the grid a returning eye reads by position does not reflow because
 * a resting HR has never been entered, and the empty slot carries the button
 * that fills it.
 */
export function CurrentAnchors({
  onAppend,
}: {
  readonly onAppend: (anchorType: WritableAnchorType) => void;
}) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>In force now</SectionLabel>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-2.5">
        {WRITABLE_ANCHOR_TYPES.map((anchorType) => (
          <CurrentAnchor
            key={anchorType}
            anchorType={anchorType}
            onAppend={onAppend}
          />
        ))}
      </div>
    </section>
  );
}

function CurrentAnchor({
  anchorType,
  onAppend,
}: {
  readonly anchorType: WritableAnchorType;
  readonly onAppend: (anchorType: WritableAnchorType) => void;
}) {
  const current = $api.useQuery("get", "/api/v1/anchors/current", {
    params: { query: { anchor_type: anchorType } },
  });
  const label = anchorLabel(anchorType);
  const version = current.data;

  return (
    <Panel
      tone="card"
      data-testid={`current-${anchorType}`}
      className="flex min-h-[128px] flex-col gap-1.5 px-4 py-3.5"
    >
      <SectionLabel level={3}>{label}</SectionLabel>

      {current.isPending ? (
        <p className="text-ink-muted text-sm">Loading…</p>
      ) : version ? (
        <>
          <p className="flex items-baseline gap-1.5">
            <span className="font-mono font-semibold text-ink text-xl tracking-[-0.01em]">
              {formatAnchorValue(version.value)}
            </span>
            <span className="font-mono text-ink-muted text-sm">
              {version.unit}
            </span>
          </p>
          <p className="flex flex-wrap items-baseline gap-x-1.5 text-2xs text-ink-faint">
            <ProvenanceMark provenance={version.provenance} />
            <span aria-hidden>·</span>
            <span className="font-mono">
              effective {formatDayMonthYear(version.effective_date)}
            </span>
          </p>
          {version.ci_low !== null || version.ci_high !== null ? (
            <p className="font-mono text-2xs text-ink-faint">
              CI {formatBound(version.ci_low)}–{formatBound(version.ci_high)}{" "}
              {version.unit}
            </p>
          ) : null}
          {version.protocol ? (
            <p className="text-2xs text-ink-muted">{version.protocol}</p>
          ) : null}
          <Button
            size="xs"
            variant="secondary"
            className="mt-auto self-start"
            onClick={() => onAppend(anchorType)}
          >
            New {label} version
          </Button>
        </>
      ) : isNotFound(current.error) ? (
        <>
          <p>
            <NotAssessed
              reason={`No ${label} version is in force`}
              className="text-xl"
            />
          </p>
          <p className="text-ink-muted text-xs">
            No {label} yet — {ANCHOR_COPY[anchorType].what}
          </p>
          <Button
            size="xs"
            className="mt-auto self-start"
            onClick={() => onAppend(anchorType)}
          >
            Add {label}
          </Button>
        </>
      ) : (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(current.error, `the ${label} anchor`)}
        </p>
      )}
    </Panel>
  );
}

/**
 * Append a version — the only write this page has for an anchor.
 *
 * Two rules are mirrored here rather than only enforced by the API. A `tested`
 * value **must** state its protocol: a test whose method is unknown cannot be
 * compared with the next one, so the form says so instead of bouncing off a
 * 422 (`AnchorVersion.__post_init__`). And the effective date is the date the
 * value *describes the athlete from*, not today — a test entered a week late
 * is back-dated to the day it was ridden, which is what keeps a score computed
 * in between reproducible.
 *
 * Everything else the API refuses — the plausibility bounds, a confidence
 * interval that does not straddle the value — is printed here as the sentence
 * the service sent. Restating those rules in the client would be a second
 * copy of the domain that goes stale the day a bound moves.
 */
export function AnchorForm({
  anchorType,
  onAnchorTypeChange,
  valueRef,
  className,
}: {
  readonly anchorType: WritableAnchorType;
  readonly onAnchorTypeChange: (anchorType: WritableAnchorType) => void;
  readonly valueRef: React.RefObject<HTMLInputElement | null>;
  readonly className?: string;
}) {
  const base = useId();
  const queryClient = useQueryClient();
  const [value, setValue] = useState("");
  const [provenance, setProvenance] = useState<Provenance>("tested");
  const [protocol, setProtocol] = useState("");
  const [effectiveDate, setEffectiveDate] = useState(todayIsoDate);
  const [ciLow, setCiLow] = useState("");
  const [ciHigh, setCiHigh] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [appended, setAppended] = useState<AnchorVersion | null>(null);

  const append = $api.useMutation("post", "/api/v1/anchors", {
    onSuccess: (version) => {
      setAppended(version);
      setValue("");
      setProtocol("");
      setCiLow("");
      setCiHigh("");
      // Back to today, like every other field goes back to empty. A date left
      // where the last append put it is the one piece of this form that keeps
      // arguing after it has been used: a correction back-dated to June would
      // silently date the next value — a test ridden today — to June as well.
      setEffectiveDate(todayIsoDate());
      // Every derived read on the page is now stale: which version is in
      // force, the history it was appended to, and the zones computed from
      // it. Three prefixes rather than one blanket invalidation, so the
      // week and the session log are not refetched because an anchor moved.
      for (const prefix of [
        CURRENT_ANCHOR_QUERY_PREFIX,
        ANCHORS_QUERY_PREFIX,
        ZONES_QUERY_PREFIX,
      ]) {
        queryClient.invalidateQueries({ queryKey: prefix });
      }
    },
  });

  const problems = [
    ...(problem ? [problem] : []),
    ...apiErrorMessages(append.error),
  ];
  const unit = ANCHOR_COPY[anchorType].unit;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setAppended(null);
    // The last server refusal was about a payload that no longer exists.
    // react-query holds `error` until the next `mutate()`, so a guard below
    // that returns early would print the stale sentence *beside* the fresh
    // one — two complaints about one form, only one of them true.
    append.reset();
    const parsed = parseNumberInput(value);
    if (parsed === null) {
      setProblem(`Enter the ${anchorLabel(anchorType)} value in ${unit}.`);
      return;
    }
    if (provenance === "tested" && protocol.trim() === "") {
      setProblem(
        "Say how it was tested. A tested value whose protocol is unknown " +
          "cannot be compared with the next test.",
      );
      return;
    }
    const parsedCiLow = parseNumberInput(ciLow);
    const parsedCiHigh = parseNumberInput(ciHigh);
    // `null` means two different things here — "no bound" and "that is not a
    // number" — and only the field's own text can tell them apart. Without
    // this, a typo'd `26o` is sent as *no* confidence interval and reported
    // back as a success, which is the one failure mode the athlete cannot see.
    if (
      (ciLow.trim() !== "" && parsedCiLow === null) ||
      (ciHigh.trim() !== "" && parsedCiHigh === null)
    ) {
      setProblem(
        `A confidence bound is a number in ${unit}, or nothing at all.`,
      );
      return;
    }
    setProblem(null);
    append.mutate({
      body: {
        anchor_type: anchorType,
        value: parsed,
        provenance,
        // Sent as null rather than omitted for symmetry with the two CI
        // bounds: the API treats both the same, and a form that omits some
        // of its own fields is a form whose payload depends on what was
        // typed rather than on what it asks for.
        protocol: protocol.trim() === "" ? null : protocol.trim(),
        // Null rather than the empty string a cleared date input holds: the
        // API's own default for an absent one is today, which is what an
        // emptied field means.
        effective_date: effectiveDate === "" ? null : effectiveDate,
        ci_low: parsedCiLow,
        ci_high: parsedCiHigh,
        // `unit` is deliberately not sent: the API stamps the anchor type's
        // own unit, and a client that names one can only ever agree or be
        // rejected.
      },
    });
  }

  return (
    <Panel className={className}>
      <form className="flex flex-col gap-3.5 px-5 py-4" onSubmit={submit}>
        <SectionLabel level={2}>Append a version</SectionLabel>
        <p className="max-w-[62ch] text-ink-muted text-sm">
          {APPEND_ONLY_COPY}
        </p>

        <FieldRow>
          <Field
            label="Anchor"
            htmlFor={`${base}-type`}
            className="flex-[1_1_150px]"
          >
            <NativeSelect
              id={`${base}-type`}
              className="w-full"
              value={anchorType}
              onChange={(event) => {
                onAnchorTypeChange(event.target.value as WritableAnchorType);
                setProblem(null);
              }}
            >
              {WRITABLE_ANCHOR_TYPES.map((option) => (
                <NativeSelectOption key={option} value={option}>
                  {anchorLabel(option)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>

          <Field
            label="Value"
            hint={unit}
            htmlFor={`${base}-value`}
            className="flex-[1_1_110px]"
          >
            <Input
              id={`${base}-value`}
              ref={valueRef}
              inputMode="decimal"
              className="font-mono"
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
                setProblem(null);
              }}
            />
          </Field>

          <Field
            label="Effective from"
            hint="the day it describes you"
            htmlFor={`${base}-effective`}
            className="flex-[1_1_150px]"
          >
            <Input
              id={`${base}-effective`}
              type="date"
              className="font-mono"
              value={effectiveDate}
              onChange={(event) => {
                setEffectiveDate(event.target.value);
                setProblem(null);
              }}
            />
          </Field>
        </FieldRow>

        <FieldRow>
          <Field
            label="Provenance"
            htmlFor={`${base}-provenance`}
            className="flex-[1_1_220px]"
          >
            <NativeSelect
              id={`${base}-provenance`}
              className="w-full"
              value={provenance}
              onChange={(event) => {
                setProvenance(event.target.value as Provenance);
                setProblem(null);
              }}
            >
              {PROVENANCE_OPTIONS.map((option) => (
                <NativeSelectOption key={option.value} value={option.value}>
                  {option.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>

          <Field
            label="Protocol"
            hint={provenance === "tested" ? "required" : "optional"}
            htmlFor={`${base}-protocol`}
            className="flex-[1_1_240px]"
          >
            <Input
              id={`${base}-protocol`}
              value={protocol}
              placeholder="20 min × 0.95"
              onChange={(event) => {
                setProtocol(event.target.value);
                setProblem(null);
              }}
            />
          </Field>
        </FieldRow>

        <FieldRow>
          <Field
            label="CI low"
            hint={`${unit}, optional`}
            htmlFor={`${base}-ci-low`}
            className="flex-[1_1_110px]"
          >
            <Input
              id={`${base}-ci-low`}
              inputMode="decimal"
              className="font-mono"
              value={ciLow}
              onChange={(event) => {
                setCiLow(event.target.value);
                setProblem(null);
              }}
            />
          </Field>
          <Field
            label="CI high"
            hint={`${unit}, optional`}
            htmlFor={`${base}-ci-high`}
            className="flex-[1_1_110px]"
          >
            <Input
              id={`${base}-ci-high`}
              inputMode="decimal"
              className="font-mono"
              value={ciHigh}
              onChange={(event) => {
                setCiHigh(event.target.value);
                setProblem(null);
              }}
            />
          </Field>
          <Button type="submit" className="ml-auto" disabled={append.isPending}>
            {append.isPending ? "Appending…" : "Append version"}
          </Button>
        </FieldRow>

        {appended ? (
          <p
            role="status"
            className="rounded-card border border-hairline-card bg-inset px-3.5 py-2.5 text-ink-secondary text-sm"
          >
            <span className="font-mono">
              {anchorLabel(appended.anchor_type)}{" "}
              {formatAnchorValue(appended.value)} {appended.unit}
            </span>{" "}
            appended, effective{" "}
            <span className="font-mono">
              {formatDayMonthYear(appended.effective_date)}
            </span>
            .{" "}
            {/* A version dated ahead of today is stored but not in force
                (`anchor_as_of`), so the card above still shows the old value.
                Said here, because a success message beside a panel that did
                not change is otherwise read as a failed save. */}
            {appended.effective_date > todayIsoDate()
              ? "It is not in force yet — the version above stands until that day."
              : ""}
          </p>
        ) : null}

        {problems.length > 0 ? (
          <ul
            role="alert"
            className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
          >
            {problems.map((entry) => (
              <li key={entry}>{entry}</li>
            ))}
          </ul>
        ) : null}
      </form>
    </Panel>
  );
}

/**
 * Every version ever appended, newest first.
 *
 * The history is the product's memory of *why* a number is what it is, so it
 * is on the page rather than behind a disclosure: the row that says an FTP was
 * assumed in January and tested in July is the answer to "why did my zones
 * move", and it is the only place that answer exists.
 *
 * Filtered through the API's own `anchor_type` parameter rather than in the
 * client, because the filter and the pager have to agree about what "20 rows"
 * means — a client-side filter would page the unfiltered history and then hide
 * most of what it fetched.
 */
export function AnchorHistory() {
  const filterId = useId();
  const [anchorType, setAnchorType] = useState<AnchorType | "">("");
  const [offset, setOffset] = useState(0);

  const history = $api.useQuery("get", "/api/v1/anchors", {
    params: {
      query: {
        offset,
        limit: HISTORY_PAGE,
        ...(anchorType === "" ? {} : { anchor_type: anchorType }),
      },
    },
  });
  const versions = history.data?.items ?? [];
  const total = history.data?.total ?? 0;
  // Read once for the whole table rather than per row, so every row of one
  // render answers "in force?" against the same day.
  const today = todayIsoDate();

  return (
    <section className="flex flex-col gap-2.5">
      <Pager
        heading="History"
        subject="anchor versions"
        offset={offset}
        onPage={versions.length}
        total={total}
        pageSize={HISTORY_PAGE}
        onOffsetChange={setOffset}
      >
        <label htmlFor={filterId} className="sr-only">
          Filter the history
        </label>
        <NativeSelect
          id={filterId}
          size="sm"
          value={anchorType}
          onChange={(event) => {
            setAnchorType(event.target.value as AnchorType | "");
            // Back to the first page: an offset means nothing once the list
            // under it is a different list.
            setOffset(0);
          }}
        >
          <NativeSelectOption value="">Every anchor</NativeSelectOption>
          {WRITABLE_ANCHOR_TYPES.map((option) => (
            <NativeSelectOption key={option} value={option}>
              {anchorLabel(option)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </Pager>

      {history.isPending ? (
        <p className="text-ink-muted text-sm">Loading the history…</p>
      ) : history.error ? (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(history.error, "the anchor history")}
        </p>
      ) : versions.length === 0 ? (
        <Panel className="px-5 py-4 text-ink-muted text-base">
          {anchorType === ""
            ? "No anchor has ever been entered. Append one above and it lands here — every version, for good."
            : `No ${anchorLabel(anchorType)} version has been entered. Append one above and it lands here.`}
        </Panel>
      ) : (
        <Panel className="overflow-x-auto">
          <table className="w-full border-collapse text-base">
            <thead>
              <tr className="border-hairline border-b text-left">
                <Th className="w-[120px]">Effective</Th>
                <Th className="w-[90px]">Anchor</Th>
                <Th className="w-[110px]">Value</Th>
                <Th className="w-[140px]">Provenance</Th>
                <Th>Protocol</Th>
                <Th className="w-[130px]">CI</Th>
                <Th className="w-[120px]">Appended · UTC</Th>
                <Th className="w-[80px]">By</Th>
              </tr>
            </thead>
            <tbody>
              {versions.map((version) => (
                <tr
                  key={version.id}
                  data-testid="anchor-version"
                  className="border-hairline-faint border-b last:border-b-0"
                >
                  <Td className="font-mono text-ink-secondary text-sm">
                    {formatDayMonthYear(version.effective_date)}
                    {/* The history sorts by effective date, so a version
                        dated ahead of today sits at the top of the table
                        while a *different* version is the one in force
                        (`anchor_as_of`). Unmarked, the first row reads as
                        the current value and contradicts the card above. */}
                    {version.effective_date > today ? (
                      <span
                        title="Stored, but dated ahead of today: the version before it is still the one in force."
                        className="block cursor-help font-sans text-2xs text-status-under"
                      >
                        not in force yet
                      </span>
                    ) : null}
                  </Td>
                  <Td className="text-ink-secondary text-sm">
                    {anchorLabel(version.anchor_type)}
                  </Td>
                  <Td className="font-mono text-ink text-sm">
                    {formatAnchorValue(version.value)} {version.unit}
                  </Td>
                  <Td className="text-sm">
                    <ProvenanceMark provenance={version.provenance} />
                  </Td>
                  <Td className="text-ink-muted text-sm">
                    {version.protocol ?? ""}
                  </Td>
                  <Td className="font-mono text-ink-muted text-sm">
                    {version.ci_low === null && version.ci_high === null
                      ? ""
                      : `${formatBound(version.ci_low)}–${formatBound(version.ci_high)}`}
                  </Td>
                  <Td className="font-mono text-ink-faint text-sm">
                    {formatUtcStamp(version.created_at)}
                  </Td>
                  {/* Who wrote it, which is not where the value came from:
                      the agent may append with any provenance, and it may
                      never claim to be the athlete (build plan §0.7). */}
                  <Td className="text-ink-faint text-sm">{version.source}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      <p className="max-w-[80ch] text-ink-faint text-xs">
        There is no edit and no delete here, and none in the API either — it
        answers a PUT, PATCH or DELETE on a version with 405 by design.{" "}
        {APPEND_ONLY_COPY}
      </p>
    </section>
  );
}

/** One half of a confidence interval, or a dash where it has no bound. */
function formatBound(value: number | null): string {
  return value === null ? "—" : formatAnchorValue(value);
}
