import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AuthGuard } from "@/components/auth/auth-guard";
import { LoginForm } from "@/components/auth/login-form";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

const push = vi.fn();
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
}));

/**
 * Which router method a component uses is asserted in its own test; this one
 * is about *where* the app sends the user, so it accepts either and fails for
 * the cache reason rather than for a navigation-style change.
 */
function navigationsTo(href: string) {
  return [...push.mock.calls, ...replace.mock.calls].filter(
    ([target]) => target === href,
  );
}

/**
 * Mirrors app/providers.tsx: the same client survives client-side navigation,
 * and `staleTime` is what makes a cached session answer outlive the login.
 * Both are load-bearing here — with a per-render client or `staleTime: 0` the
 * regression below cannot reproduce.
 */
function appQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: false },
      mutations: { retry: false },
    },
  });
}

/** Session answers follow the mock backend's own state, as a browser would. */
function statefulAuthBackend() {
  let hasSession = false;
  server.use(
    http.get("/api/v1/auth/session", ({ response }) =>
      response(200).json({ authenticated: hasSession }),
    ),
    http.post("/api/v1/auth/login", ({ response }) => {
      hasSession = true;
      return response(204).empty();
    }),
  );
}

describe("login flow", () => {
  it("reaches the guarded app after a bounce to /login", async () => {
    const user = userEvent.setup();
    const queryClient = appQueryClient();
    statefulAuthBackend();

    // 1. A logged-out visitor hits a guarded page and is sent to /login. This
    //    is what seeds the cache with `{ authenticated: false }`.
    const guard = render(
      <QueryClientProvider client={queryClient}>
        <AuthGuard>
          <p>secret content</p>
        </AuthGuard>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(navigationsTo("/login")).not.toHaveLength(0));
    guard.unmount();

    // 2. They sign in successfully on /login.
    render(
      <QueryClientProvider client={queryClient}>
        <LoginForm />
      </QueryClientProvider>,
    );
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(navigationsTo("/")).not.toHaveLength(0));

    // 3. The guarded page mounts again against the same cache. It must let
    //    them in: serving the still-fresh pre-login `false` bounces them back
    //    to /login for as long as `staleTime` lasts.
    push.mockClear();
    replace.mockClear();
    render(
      <QueryClientProvider client={queryClient}>
        <AuthGuard>
          <p>secret content</p>
        </AuthGuard>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("secret content")).toBeInTheDocument();
    expect(navigationsTo("/login")).toHaveLength(0);
  });
});
