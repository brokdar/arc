"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { sessionExportUrl } from "@/lib/exports";

export interface ExportActionProps {
  readonly sessionId: string;
}

type CopyState =
  | { readonly kind: "idle" }
  | { readonly kind: "copied" }
  | { readonly kind: "failed"; readonly reason: string };

const LINK_CLASS =
  "rounded-md px-2.5 py-1.5 text-left text-ink text-sm hover:bg-raised-hover";

/**
 * One workout out of arc in two taps: *Export*, then what to do with it.
 *
 * The direction's bar (§11.2, S30.2) is two taps from the session page, and
 * the most-asked use is handing one workout to an AI assistant — so the first
 * choice in the panel is *Copy as text*, which leaves the text on the
 * clipboard ready to paste, and the two downloads follow. An inline panel
 * under its trigger, not a modal (UI convention 2): nothing here edits a
 * record.
 *
 * The copy fetches the same `GET /sessions/{id}/export?format=text` the
 * download links to, so what is pasted and what is saved are one text.
 */
export function ExportAction({ sessionId }: ExportActionProps) {
  const [open, setOpen] = useState(false);
  const [copy, setCopy] = useState<CopyState>({ kind: "idle" });
  const panelId = useId();

  async function copyText() {
    const { data, error } = await apiClient.GET(
      "/api/v1/sessions/{session_id}/export",
      {
        params: { path: { session_id: sessionId }, query: { format: "text" } },
        parseAs: "text",
      },
    );
    if (error !== undefined || data === undefined) {
      setCopy({
        kind: "failed",
        reason: apiErrorMessages(error)[0] ?? "the server sent nothing",
      });
      return;
    }
    try {
      await navigator.clipboard.writeText(data);
      setCopy({ kind: "copied" });
    } catch {
      setCopy({
        kind: "failed",
        reason: "this browser refused clipboard access; use Download text",
      });
    }
  }

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          setOpen((value) => !value);
          setCopy({ kind: "idle" });
        }}
      >
        Export
      </Button>
      {open ? (
        <fieldset
          id={panelId}
          aria-label="Export this session"
          className="absolute right-0 z-10 mt-1.5 flex w-56 min-w-0 flex-col gap-0.5 rounded-lg border border-hairline-strong bg-panel p-1.5"
        >
          <button
            type="button"
            className={LINK_CLASS}
            onClick={() => void copyText()}
          >
            Copy as text
          </button>
          <a
            className={LINK_CLASS}
            href={sessionExportUrl(sessionId, "text")}
            download
          >
            Download text
          </a>
          <a
            className={LINK_CLASS}
            href={sessionExportUrl(sessionId, "csv")}
            download
          >
            Download CSV
          </a>
          <p role="status" className="px-2.5 pt-1 text-ink-muted text-xs">
            {copy.kind === "copied" ? "Copied" : null}
            {copy.kind === "failed" ? `Could not copy: ${copy.reason}` : null}
          </p>
        </fieldset>
      ) : null}
    </div>
  );
}
