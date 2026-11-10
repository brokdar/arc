import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InstallHint } from "@/components/shell/install-hint";

/**
 * iOS has no install prompt, and an app added to the home screen does not
 * share Safari's cookies — so the athlete who logged in in Safari opens the
 * installed app to a login page and thinks the sign-in was lost. The hint
 * says both halves: how to install, and to log in inside the installed app.
 */

const IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const ANDROID =
  "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36";

function asBrowser(userAgent: string, standalone: boolean) {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);
  Object.defineProperty(navigator, "standalone", {
    configurable: true,
    value: standalone,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "standalone");
});

describe("InstallHint", () => {
  it("tells an iPhone in Safari how to install and to log in inside the app", async () => {
    asBrowser(IPHONE, false);
    render(<InstallHint />);

    const hint = await screen.findByRole("note", { name: "Install arc" });
    expect(hint).toHaveTextContent("Share");
    expect(hint).toHaveTextContent("Add to Home Screen");
    expect(hint).toHaveTextContent("log in inside the installed app");
  });

  it("stays away once arc runs from the home screen", () => {
    asBrowser(IPHONE, true);
    render(<InstallHint />);

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("stays away on a browser that offers its own install prompt", () => {
    asBrowser(ANDROID, false);
    render(<InstallHint />);

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("goes away when dismissed and stays away on the next visit", async () => {
    asBrowser(IPHONE, false);
    const first = render(<InstallHint />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Dismiss install hint" }),
    );
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    first.unmount();

    render(<InstallHint />);

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});
