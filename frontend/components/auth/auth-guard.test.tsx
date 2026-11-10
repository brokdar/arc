import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGuard } from "@/components/auth/auth-guard";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
}));

function renderGuard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthGuard>
        <p>secret content</p>
      </AuthGuard>
    </QueryClientProvider>,
  );
}

describe("AuthGuard", () => {
  beforeEach(() => {
    replace.mockClear();
  });

  it("renders children for an authenticated session", async () => {
    renderGuard();

    expect(await screen.findByText("secret content")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("shows a loading state while the session is being checked", () => {
    renderGuard();

    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("redirects to /login when there is no session", async () => {
    server.use(
      http.get("/api/v1/auth/session", ({ response }) =>
        response(200).json({ authenticated: false }),
      ),
    );

    renderGuard();

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("reports an unreachable API instead of redirecting", async () => {
    server.use(
      http.untyped.get("http://localhost:8000/api/v1/auth/session", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderGuard();

    expect(
      await screen.findByText(/Could not verify your session/),
    ).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});

describe("AuthGuard with no network", () => {
  beforeEach(() => {
    replace.mockClear();
  });

  function goOffline() {
    server.use(
      http.untyped.get("http://localhost:8000/api/v1/auth/session", () =>
        HttpResponse.error(),
      ),
    );
  }

  it("opens the app on a phone that was signed in on its last online load", async () => {
    const online = renderGuard();
    await screen.findByText("secret content");
    online.unmount();

    goOffline();
    renderGuard();

    expect(await screen.findByText("secret content")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("does not open the app on a phone that was never signed in", async () => {
    goOffline();
    renderGuard();

    expect(
      await screen.findByText(/Could not verify your session/),
    ).toBeInTheDocument();
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("forgets the sign-in once the server says the session is gone", async () => {
    const online = renderGuard();
    await screen.findByText("secret content");
    online.unmount();

    server.use(
      http.get("/api/v1/auth/session", ({ response }) =>
        response(200).json({ authenticated: false }),
      ),
    );
    const signedOut = renderGuard();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    signedOut.unmount();

    goOffline();
    renderGuard();

    expect(
      await screen.findByText(/Could not verify your session/),
    ).toBeInTheDocument();
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("does not open the app when the server answers with an error", async () => {
    const online = renderGuard();
    await screen.findByText("secret content");
    online.unmount();

    server.use(
      http.untyped.get("http://localhost:8000/api/v1/auth/session", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderGuard();

    expect(
      await screen.findByText(/Could not verify your session/),
    ).toBeInTheDocument();
  });
});
