"use client";

import { useQueryClient } from "@tanstack/react-query";

import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import {
  AGENT_NOTES_QUERY_PREFIX,
  type AgentNote,
  type DisputeRating,
  NOTE_KIND_LABELS,
  nextRating,
} from "@/lib/agent-notes";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";
import { formatUtcStamp } from "@/lib/format";
import { actorLabel } from "@/lib/proposals";
import { cn } from "@/lib/utils";

/**
 * What the coach has said about one session, or about one week.
 *
 * Everything here is drawn in the reserved purple (`--color-coach*`), and that
 * is not decoration: build-plan invariant 7 requires agent-written text to be
 * distinguishable from computed findings *wherever it lands*, and this panel
 * sits directly under panels full of numbers the domain derived. The tint, the
 * border and the ink together are the sentence "a language model wrote this",
 * said without a paragraph of chrome saying it.
 *
 * **The panel is absent when there is nothing to show.** Not an empty state
 * with a remedy beside it (UI convention 3), because the remedy is not in this
 * application: notes arrive over MCP from a coaching agent the athlete may
 * never connect, and a permanent "no coach notes yet" block on every session
 * of an athlete who has not connected one is a dead slot on every page rather
 * than a missing input with an action attached. A *failed* load is
 * different and does render — "the coach said nothing" and "we could not ask"
 * are not the same claim.
 */
export function SessionCoachNotes({ sessionId }: { sessionId: string }) {
  const query = $api.useQuery("get", "/api/v1/agent-notes", {
    params: { query: { session_id: sessionId } },
  });
  return <CoachNotes query={query} subject="this session's coach notes" />;
}

/** The same panel, for a week — what the coach made of the block as a whole. */
export function WeekCoachNotes({ week }: { week: string }) {
  const query = $api.useQuery("get", "/api/v1/agent-notes", {
    params: { query: { week } },
  });
  return <CoachNotes query={query} subject="this week's coach notes" />;
}

interface NotesQuery {
  readonly data?: { readonly items: readonly AgentNote[] };
  readonly error: unknown;
}

function CoachNotes({
  query,
  subject,
}: {
  query: NotesQuery;
  subject: string;
}) {
  const notes = query.data?.items ?? [];

  if (query.error) {
    return (
      <section className="flex flex-col gap-2.5">
        <SectionLabel level={2} className="text-coach">
          Coach
        </SectionLabel>
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(query.error, subject)}
        </p>
      </section>
    );
  }

  if (notes.length === 0) {
    return null;
  }

  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2} className="text-coach">
        Coach
      </SectionLabel>
      <ul className="flex flex-col gap-2">
        {notes.map((note) => (
          <li key={note.id}>
            <NoteCard note={note} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * One note: what was said, who said it, and the athlete's answer to it.
 *
 * The attribution line is mandatory and is *above* the text rather than under
 * it — a reader who has already absorbed a paragraph as fact and is then told
 * a model wrote it has read it as fact. Model id, key label and instant, all
 * three, because "the coach" is not one thing over a season: a note written by
 * a model that has since been replaced is still on the session, and the only
 * way to weigh it later is to know which one wrote it and when.
 */
function NoteCard({ note }: { note: AgentNote }) {
  const queryClient = useQueryClient();
  const dispute = $api.useMutation(
    "post",
    "/api/v1/agent-notes/{note_id}/dispute",
    {
      onSuccess: () =>
        queryClient.invalidateQueries({ queryKey: AGENT_NOTES_QUERY_PREFIX }),
    },
  );

  const rate = (tapped: DisputeRating) =>
    dispute.mutate({
      params: { path: { note_id: note.id } },
      body: { rating: nextRating(note.dispute, tapped) },
    });

  // A rating that did not land has to say so. The button's pressed state comes
  // from the note the server returns, so a refused tap leaves the toggle
  // exactly as it was — indistinguishable, without this line, from a tap the
  // page never received.
  const problems = apiErrorMessages(dispute.error);

  return (
    <article
      data-testid="coach-note"
      data-kind={note.kind}
      className="flex flex-col gap-2 rounded-card border border-coach-border bg-coach-surface px-4 py-3.5"
    >
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="rounded-badge bg-coach-tint px-1.5 py-0.5 font-medium text-2xs text-coach-strong">
          {NOTE_KIND_LABELS[note.kind]}
        </span>
        <span className="font-mono text-2xs text-coach">{note.model_id}</span>
        <span className="font-mono text-2xs text-ink-faint">
          {actorLabel(note.created_by)}
        </span>
        <span className="ml-auto font-mono text-2xs text-ink-faint">
          {formatUtcStamp(note.created_at)}
        </span>
      </div>

      <p className="max-w-[76ch] text-base text-coach-ink leading-relaxed">
        {note.text}
      </p>

      {note.cites.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <SectionLabel className="text-coach">Cites</SectionLabel>
          {note.cites.map((cite) => (
            <span
              key={cite}
              title={cite}
              className="rounded-badge border border-coach-border px-1.5 py-0.5 font-mono text-2xs text-ink-muted"
            >
              {cite.slice(0, 8)}
            </span>
          ))}
        </div>
      ) : (
        // The coach cited nothing. Said plainly rather than left blank: a note
        // resting on no artefact is a weaker claim than one that points at a
        // session, and the difference is exactly what the athlete is being
        // asked to rate.
        <span className="text-2xs text-ink-faint">Cites nothing.</span>
      )}

      <div className="flex items-center gap-1.5 border-coach-border border-t pt-2.5">
        <span className="mr-auto text-2xs text-ink-faint">
          {note.dispute === null
            ? "Was this useful?"
            : `You rated this ${note.dispute === "up" ? "useful" : "wrong"}${
                note.disputed_at ? ` · ${formatUtcStamp(note.disputed_at)}` : ""
              } — tap again to take it back.`}
        </span>
        <RatingButton
          note={note}
          rating="up"
          label="Useful"
          glyph="👍"
          busy={dispute.isPending}
          onRate={rate}
        />
        <RatingButton
          note={note}
          rating="down"
          label="Wrong"
          glyph="👎"
          busy={dispute.isPending}
          onRate={rate}
        />
      </div>

      {problems.length > 0 ? (
        <p role="alert" className="text-2xs text-destructive">
          {problems.join(" ")}
        </p>
      ) : null}
    </article>
  );
}

/**
 * One half of the toggle.
 *
 * `aria-pressed` rather than two mutually exclusive radio buttons, because the
 * control has three states and only two buttons: neither pressed is "no
 * rating", which a radio group cannot express once a choice has been made.
 * The glyph is decorative and the *name* carries the meaning, so a screen
 * reader hears "Useful, pressed" rather than "thumbs up emoji".
 */
function RatingButton({
  note,
  rating,
  label,
  glyph,
  busy,
  onRate,
}: {
  note: AgentNote;
  rating: DisputeRating;
  label: string;
  glyph: string;
  busy: boolean;
  onRate: (rating: DisputeRating) => void;
}) {
  const pressed = note.dispute === rating;
  return (
    <Button
      type="button"
      variant="ghost"
      size="xs"
      aria-pressed={pressed}
      aria-label={label}
      disabled={busy}
      className={cn(
        pressed ? "bg-coach-tint text-coach-strong" : "text-ink-muted",
      )}
      onClick={() => onRate(rating)}
    >
      <span aria-hidden>{glyph}</span>
    </Button>
  );
}
