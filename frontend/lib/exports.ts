import { env } from "@/env";

/**
 * Where each export downloads from.
 *
 * Downloads are plain links rather than fetches: the API sends every export
 * with `Content-Disposition: attachment`, so the browser saves the file under
 * its own name with the session cookie attached, and a whole training history
 * never has to pass through JavaScript memory. Only "Copy as text" fetches,
 * because the clipboard needs the text in hand.
 *
 * Built on the same base URL the API client uses, so a same-origin build
 * (empty base, behind Caddy) links to `/api/v1/...` on the page's own host.
 */
const API = `${env.NEXT_PUBLIC_API_BASE_URL}/api/v1`;

export type CsvVariant = "comma" | "semicolon";
export type SessionExportFormat = "text" | "csv";

/** Every session with sets, as openweight JSON with arc's extensions. */
export const WORKOUTS_JSON_URL = `${API}/exports/workouts.json`;

/** Every logged set, one row each. */
export function setsCsvUrl(variant: CsvVariant): string {
  return `${API}/exports/sets.csv?variant=${variant}`;
}

/** Every logged set, in Strong's columns (what Hevy and others import). */
export const STRONG_CSV_URL = `${API}/exports/strong.csv`;

/** One session, as text or as the set-level CSV. */
export function sessionExportUrl(
  sessionId: string,
  format: SessionExportFormat,
): string {
  return `${API}/sessions/${sessionId}/export?format=${format}`;
}
