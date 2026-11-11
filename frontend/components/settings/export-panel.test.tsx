import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExportPanel } from "@/components/settings/export-panel";

const API = "http://localhost:8000/api/v1";

describe("export panel", () => {
  it("links every whole-history export as a download", () => {
    render(<ExportPanel />);
    const panel = screen.getByRole("region", { name: "Your data" });

    const expected: [string, string][] = [
      ["Everything (JSON)", `${API}/exports/workouts.json`],
      ["Every set (CSV)", `${API}/exports/sets.csv?variant=comma`],
      [
        "Every set (CSV, semicolon and decimal comma)",
        `${API}/exports/sets.csv?variant=semicolon`,
      ],
      ["Strong format (CSV)", `${API}/exports/strong.csv`],
    ];
    for (const [name, href] of expected) {
      const link = within(panel).getByRole("link", { name });
      expect(link).toHaveAttribute("href", href);
      expect(link).toHaveAttribute("download");
    }
  });

  it("says the Strong format loses detail", () => {
    render(<ExportPanel />);

    expect(
      screen.getByText(/drops sides and most set kinds/),
    ).toBeInTheDocument();
  });
});
