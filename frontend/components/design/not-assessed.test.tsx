import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NotAssessed } from "@/components/design/not-assessed";

describe("NotAssessed", () => {
  it("carries its reason on hover and to a screen reader, not just one of them", () => {
    render(<NotAssessed reason="No FTP anchor pinned" />);

    const placeholder = screen.getByLabelText(
      "Not assessed: No FTP anchor pinned",
    );
    expect(placeholder).toHaveTextContent("—");
    // A reason only a sighted mouse user can reach is not a reason the
    // application gave.
    expect(placeholder).toHaveAttribute("title", "No FTP anchor pinned");
  });
});
