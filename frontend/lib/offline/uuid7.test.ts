import { describe, expect, it } from "vitest";

import { isUuid } from "@/lib/ids";
import { uuid7 } from "@/lib/offline/uuid7";

describe("uuid7", () => {
  it("is a version-7, RFC-variant uuid carrying the millisecond", () => {
    const at = Date.UTC(2026, 10, 10, 9, 30);
    const id = uuid7(at);

    expect(isUuid(id)).toBe(true);
    expect(id[14]).toBe("7");
    expect("89ab").toContain(id[19]);
    expect(Number.parseInt(id.replaceAll("-", "").slice(0, 12), 16)).toBe(at);
  });

  it("sorts in minting order within a millisecond and across a clock step back", () => {
    const at = Date.UTC(2026, 10, 10, 9, 30);
    const ids = [
      uuid7(at + 10_000),
      uuid7(at + 10_000),
      uuid7(at + 10_000),
      // The device clock stepped back a second.
      uuid7(at + 9_000),
      uuid7(at + 9_000),
    ];

    expect([...ids].sort()).toEqual(ids);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
