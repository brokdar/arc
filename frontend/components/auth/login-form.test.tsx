import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

async function submitPassword(password: string) {
  const user = userEvent.setup();
  renderWithQuery(<LoginForm />);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginForm", () => {
  beforeEach(() => {
    push.mockClear();
  });

  it("navigates home after a successful login", async () => {
    await submitPassword("correct-horse");

    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an inline error and stays put on a rejected password", async () => {
    server.use(
      http.post("/api/v1/auth/login", ({ response }) =>
        response(401).json({ detail: "Invalid password" }),
      ),
    );

    await submitPassword("wrong");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Incorrect password.",
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("distinguishes an unreachable API from a bad password", async () => {
    server.use(
      http.untyped.post("http://localhost:8000/api/v1/auth/login", () =>
        HttpResponse.error(),
      ),
    );

    await submitPassword("anything");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the server.",
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("disables the form while the request is in flight", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/auth/login", async ({ response }) => {
        await held;
        return response(204).empty();
      }),
    );

    renderWithQuery(<LoginForm />);
    await user.type(screen.getByLabelText("Password"), "slow");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const pending = await screen.findByRole("button", { name: "Signing in…" });
    expect(pending).toBeDisabled();

    release();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  });
});
