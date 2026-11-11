/**
 * UUIDv7 ids minted on the phone, before there is any signal.
 *
 * Every session, set and outbox item gets one the moment it exists, so the
 * server can recognise it when it arrives twice (`POST /sync` upserts on it).
 * Version 7 because its first 48 bits are the millisecond it was minted, so
 * ids sort in creation order — the same kind the server mints
 * (`app/persistence/types.py`).
 *
 * **Monotonic within one page.** Two ids minted in one millisecond, or after
 * the device clock stepped backwards, still sort in minting order: the
 * timestamp never goes below the last one used, and the 12-bit counter after
 * it increments (RFC 9562 §6.2, method 1). When the counter runs out the
 * timestamp is advanced by one millisecond rather than wrapping.
 */

let lastMs = -1;
let counter = 0;

function random(bytes: number): Uint8Array {
  const out = new Uint8Array(bytes);
  crypto.getRandomValues(out);
  return out;
}

/** A new uuid7 string, later-sorting than every id minted before it here. */
export function uuid7(now: number = Date.now()): string {
  let ms = Math.max(Math.floor(now), lastMs);
  if (ms === lastMs) {
    counter += 1;
    if (counter > 0xfff) {
      ms += 1;
      counter = random(2)[0] & 0x7f;
    }
  } else {
    counter = random(2)[0] & 0x7f;
  }
  lastMs = ms;

  const bytes = random(16);
  // 48-bit big-endian millisecond timestamp.
  for (let i = 5; i >= 0; i--) {
    bytes[i] = ms % 256;
    ms = Math.floor(ms / 256);
  }
  bytes[6] = 0x70 | ((counter >> 8) & 0x0f);
  bytes[7] = counter & 0xff;
  bytes[8] = 0x80 | (bytes[8] & 0x3f);

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
