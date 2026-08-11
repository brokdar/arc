"use client";

import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import {
  type WritableAnchorType,
  ZONE_MODEL_LABELS,
  ZONE_PREVIEWS,
  type ZoneModel,
} from "@/lib/anchors";
import { $api } from "@/lib/api/client";
import { isNotFound, loadFailureMessage } from "@/lib/api-errors";
import { formatAnchorValue, formatPercent } from "@/lib/format";
import { anchorLabel } from "@/lib/targets";

/**
 * What the anchors in force actually produce.
 *
 * The zones are **fetched, never computed here** (`GET /api/v1/zones`): they
 * are a pure function of one anchor version and one declared model
 * (`app.domain.zones`), and a client that multiplied the percentages itself
 * would be a second scheme table free to drift from the one every
 * time-in-zone metric is measured against.
 *
 * It sits beside the append form on purpose. This is the page's answer to
 * "what does entering an FTP get me" — and when there is no FTP, the empty
 * state says exactly that, with the control that fixes it right beside it.
 */
export function ZonesPreview({
  onAppend,
  className,
}: {
  readonly onAppend: (anchorType: WritableAnchorType) => void;
  readonly className?: string;
}) {
  return (
    <Panel className={className}>
      <div className="flex flex-col gap-4 px-5 py-4">
        <SectionLabel level={2}>Zones in force</SectionLabel>
        {ZONE_PREVIEWS.map((preview) => (
          <ZoneTable
            key={preview.model}
            anchorType={preview.anchorType}
            model={preview.model}
            heading={preview.heading}
            onAppend={onAppend}
          />
        ))}
      </div>
    </Panel>
  );
}

function ZoneTable({
  anchorType,
  model,
  heading,
  onAppend,
}: {
  readonly anchorType: WritableAnchorType;
  readonly model: ZoneModel;
  readonly heading: string;
  readonly onAppend: (anchorType: WritableAnchorType) => void;
}) {
  // The model is named rather than left to default. It defaults to exactly
  // this one (`app.domain.zones.DEFAULT_ZONE_MODEL`), but the scheme a number
  // was derived under is part of the claim (spec §2: "the zone model in use
  // is declared"), and a request that states it cannot silently start meaning
  // something else when the default moves.
  const zones = $api.useQuery("get", "/api/v1/zones", {
    params: { query: { anchor_type: anchorType, zone_model: model } },
  });
  const label = anchorLabel(anchorType);

  return (
    <section data-testid={`zones-${model}`} className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <SectionLabel level={3}>{heading}</SectionLabel>
        <span className="font-mono text-2xs text-ink-faint">
          {ZONE_MODEL_LABELS[model]}
        </span>
      </div>

      {zones.isPending ? (
        <p className="text-ink-muted text-sm">Loading the zones…</p>
      ) : zones.data ? (
        <>
          <p className="font-mono text-2xs text-ink-faint">
            from {label} {formatAnchorValue(zones.data.anchor_version.value)}{" "}
            {zones.data.anchor_version.unit}
          </p>
          <ul className="flex flex-col">
            {zones.data.zones.map((zone) => (
              <li
                key={zone.index}
                className="flex items-baseline gap-2 border-hairline-faint border-b py-1 last:border-b-0"
              >
                <span className="w-6 shrink-0 font-mono text-2xs text-ink-faint">
                  Z{zone.index}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink-secondary text-sm">
                  {zone.name}
                </span>
                <span className="font-mono text-ink text-sm">
                  {band(whole(zone.lower), whole(zone.upper))} {zone.unit}
                </span>
                <span className="w-[76px] shrink-0 text-right font-mono text-2xs text-ink-faint">
                  {band(
                    formatPercent(zone.lower_pct),
                    zone.upper_pct === null
                      ? null
                      : formatPercent(zone.upper_pct),
                  )}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : isNotFound(zones.error) ? (
        <div className="flex flex-col items-start gap-2 rounded-button border border-hairline-faint bg-inset px-3.5 py-3">
          <p className="text-ink-muted text-sm">
            No {label} yet — add one to see {heading.toLowerCase()}. Nothing
            derives from an anchor that is not in force, so these bands stay
            empty rather than falling back to a population guess.
          </p>
          <Button size="xs" onClick={() => onAppend(anchorType)}>
            Add {label}
          </Button>
        </div>
      ) : (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(zones.error, `the ${heading.toLowerCase()}`)}
        </p>
      )}
    </section>
  );
}

/**
 * A zone bound at the resolution the athlete rides to.
 *
 * Whole watts and whole beats: the API returns `lower_pct * value` unrounded,
 * so Z2 of a 265 W FTP starts at 145.75 W — a decimal that is noise beside a
 * number nobody can hold a bike to.
 */
function whole(value: number | null): string | null {
  return value === null ? null : String(Math.round(value));
}

/**
 * One half-open band, `lower–upper`, with the top zone left open-ended.
 *
 * `≥` rather than a made-up ceiling: the scheme's last zone genuinely has
 * none (`app.domain.zones` — there is no ceiling on a sprint), and printing
 * one would be this component inventing a bound the model does not state.
 */
function band(lower: string | null, upper: string | null): string {
  return upper === null ? `≥ ${lower}` : `${lower}–${upper}`;
}
