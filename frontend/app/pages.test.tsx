import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ItemsPage from "@/app/items/page";
import LoginPage from "@/app/login/page";
import Home from "@/app/page";
import { Providers } from "@/app/providers";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

/**
 * Render smoke tests for the route shells. They are server components, but
 * synchronous and prop-free, so they render here like any other function
 * component — enough to catch a page that no longer mounts its content.
 * `Providers` supplies the QueryClient the guarded pages need.
 */
describe("route shells", () => {
  it("renders the login page", () => {
    render(
      <Providers>
        <LoginPage />
      </Providers>,
    );

    expect(
      screen.getByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
  });

  it("renders the home page behind the auth guard", async () => {
    render(
      <Providers>
        <Home />
      </Providers>,
    );

    expect(
      await screen.findByRole("heading", { name: "arc" }),
    ).toBeInTheDocument();
  });

  it("renders the items page behind the auth guard", async () => {
    render(
      <Providers>
        <ItemsPage />
      </Providers>,
    );

    expect(
      await screen.findByRole("heading", { name: "Items" }),
    ).toBeInTheDocument();
  });
});
