import { beforeEach, describe, expect, it } from "vitest";

import {
  keepAddFlowOpen,
  redirectEligible,
  redirectUriFor,
  rememberAddFlow,
  takeAddFlow,
} from "@/lib/dropbox-redirect";

/**
 * The same table `backend/tests/unit/test_connectors_dropbox.py` walks.
 *
 * The two copies of this rule are only safe while they agree, so they are
 * asserted against the same URIs — a change to one that is not made in the
 * other fails here or there rather than reaching an athlete as a 422 behind a
 * button arc should never have offered.
 */
describe("which deployments Dropbox will redirect back to", () => {
  it.each([
    "https://arc.example.com",
    "https://arc.local",
    "https://arc.example.com:8443",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://[::1]:3000",
  ])("takes the redirect flow at %s", (origin) => {
    expect(redirectEligible(origin)).toBe(true);
  });

  it.each([
    // The deployment the fallback exists for.
    "http://192.168.1.50",
    "http://arc.local",
    "http://10.0.0.4:3000",
    // A prefix match would have called these the loopback.
    "http://localhost.evil.example",
    "http://127.0.0.1.evil.example",
    "ftp://arc.example.com",
    "not a url",
    "",
  ])("falls back to the paste flow at %s", (origin) => {
    expect(redirectEligible(origin)).toBe(false);
  });

  it("builds the redirect URI the athlete registers with Dropbox", () => {
    expect(redirectUriFor("https://arc.example.com")).toBe(
      "https://arc.example.com/settings/dropbox/callback",
    );
    expect(redirectUriFor("http://localhost:3000")).toBe(
      "http://localhost:3000/settings/dropbox/callback",
    );
  });
});

describe("the marker that resumes the add flow", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("carries the integration across the trip to Dropbox, once", () => {
    rememberAddFlow("wahoo");

    expect(takeAddFlow()).toEqual({ kind: "wahoo" });
    // Taken, not read: a panel that reopened the flow on every mount would
    // trap the athlete in it.
    expect(takeAddFlow()).toBeNull();
  });

  it("has nothing to resume when the flow never started here", () => {
    expect(takeAddFlow()).toBeNull();
  });

  it("keeps a remembered integration rather than flattening it", () => {
    rememberAddFlow("wahoo");
    keepAddFlowOpen();

    expect(takeAddFlow()).toEqual({ kind: "wahoo" });
  });

  it("opens the flow at the catalogue when nothing was remembered", () => {
    keepAddFlowOpen();

    expect(takeAddFlow()).toEqual({ kind: null });
  });

  it("survives a marker it did not write", () => {
    window.sessionStorage.setItem("arc.dropbox-connect.resume", "{not json");

    expect(takeAddFlow()).toEqual({ kind: null });
  });
});
