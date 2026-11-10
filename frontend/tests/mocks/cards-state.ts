import type { components } from "@/generated/api/schema";
import { addDays, todayIsoDate } from "@/lib/dates";

type Card = components["schemas"]["CardRead"];

/** States a card can still be answered in — `OPEN_STATES` in `domain/cards.py`. */
const OPEN = new Set<Card["state"]>(["queued", "shown", "snoozed"]);

/** The seven days a card lives after it was first shown (`CARD_LIFETIME`). */
const LIFETIME_MS = 7 * 24 * 60 * 60 * 1000;

let cards: Card[] = [];

/**
 * Queue one card the way arc does: never shown, so no stamps yet.
 *
 * `CardService.create` is the only writer and always starts a card `queued`
 * with null `first_shown`, `expires_at` and `snoozed_until`, so those are not
 * overridable here — a fixture that arrived already stamped would be a card
 * the API cannot hold.
 */
export function queueCard(
  card: Pick<Card, "id" | "headline" | "because"> &
    Partial<Pick<Card, "kind" | "priority" | "payload">>,
): Card {
  const queued: Card = {
    kind: "note",
    priority: 4,
    payload: {},
    ...card,
    state: "queued",
    first_shown: null,
    expires_at: null,
    snoozed_until: null,
  };
  cards = [...cards, queued];
  return queued;
}

/**
 * `GET /cards`: at most one open, un-snoozed card, stamped as shown.
 *
 * The budget itself is the backend's (`domain/cards.py`) and is not re-derived
 * here; with the one or two cards a component test queues, "the first open
 * card by priority, then queue order" is the answer the real budget gives.
 */
export function showCards(now: Date = new Date()): Card[] {
  const open = cards
    .filter((card) => OPEN.has(card.state))
    .filter(
      (card) =>
        card.snoozed_until === null || new Date(card.snoozed_until) <= now,
    )
    .sort((a, b) => a.priority - b.priority);
  const top = open[0];
  if (!top) {
    return [];
  }
  const firstShown = top.first_shown ?? now.toISOString();
  const shown: Card = {
    ...top,
    state: "shown",
    first_shown: firstShown,
    expires_at: new Date(
      new Date(firstShown).getTime() + LIFETIME_MS,
    ).toISOString(),
    snoozed_until: null,
  };
  replace(shown);
  return [shown];
}

/**
 * `POST /cards/{id}/act`: the card as the service leaves it, or the status the
 * API answers with — 404 for an unknown id, 409 for one already answered.
 */
export function actOnCard(
  id: string,
  action: components["schemas"]["CardAction"],
  timeZone: string,
  now: Date = new Date(),
): { status: 200; card: Card } | { status: 404 | 409; detail: string } {
  const card = cards.find((entry) => entry.id === id);
  if (!card) {
    return { status: 404, detail: `Card ${id} not found` };
  }
  if (!OPEN.has(card.state)) {
    return {
      status: 409,
      detail: `This card is already ${card.state}; it cannot be answered.`,
    };
  }
  const answered: Card = {
    ...card,
    state:
      action === "accept"
        ? "accepted"
        : action === "dismiss"
          ? "dismissed"
          : "snoozed",
    first_shown: card.first_shown ?? now.toISOString(),
    snoozed_until:
      action === "snooze" ? nextLocalMidnight(now, timeZone) : null,
  };
  replace(answered);
  return { status: 200, card: answered };
}

/** Every card as the mock holds it, for assertions. */
export function cardState(): readonly Card[] {
  return cards;
}

/** Empty the queue. Wired into `resetMockState`. */
export function resetCardState(): void {
  cards = [];
}

function replace(card: Card): void {
  cards = cards.map((entry) => (entry.id === card.id ? card : entry));
}

/**
 * The athlete's next midnight as an ISO instant — where a snooze ends
 * (`next_local_midnight` in `domain/cards.py`).
 */
function nextLocalMidnight(now: Date, timeZone: string): string {
  const tomorrow = addDays(todayIsoDate(timeZone, now), 1);
  const guess = Date.parse(`${tomorrow}T00:00:00Z`);
  return new Date(guess - zoneOffsetMs(guess, timeZone)).toISOString();
}

/** How far ``timeZone`` is ahead of UTC at ``instant``, in milliseconds. */
function zoneOffsetMs(instant: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((entry) => entry.type === type)?.value);
  return (
    Date.UTC(
      part("year"),
      part("month") - 1,
      part("day"),
      part("hour"),
      part("minute"),
      part("second"),
    ) - instant
  );
}
