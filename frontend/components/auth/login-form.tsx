"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import type { MethodResponse } from "openapi-react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { $api } from "@/lib/api/client";

/**
 * The cache key `AuthGuard`'s `$api.useQuery("get", "/api/v1/auth/session")`
 * writes under. Derived from `queryOptions` rather than spelled out as a
 * literal so it cannot drift from openapi-react-query's key derivation.
 */
const sessionQueryKey = $api.queryOptions(
  "get",
  "/api/v1/auth/session",
).queryKey;

type Session = MethodResponse<typeof $api, "get", "/api/v1/auth/session">;

/**
 * The whole login UI: one password, one button. There is a single user and no
 * user table — the backend compares against a bcrypt hash from its
 * environment and answers with a signed session cookie.
 */
export function LoginForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const login = $api.useMutation("post", "/api/v1/auth/login");

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate(
      { body: { password } },
      {
        onSuccess: () => {
          // A logged-out visitor who was bounced here left `{authenticated:
          // false}` in the cache, and the app's queries have a 30s staleTime
          // (app/providers.tsx). Navigating without correcting that entry lets
          // AuthGuard re-serve the stale `false` and redirect right back to
          // /login. The 204 we just got is proof of a session, so write it.
          queryClient.setQueryData<Session>(sessionQueryKey, {
            authenticated: true,
          });
          // replace, not push: /login must not sit in history behind the app,
          // where Back would land on it while already signed in.
          router.replace("/");
        },
      },
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full max-w-sm space-y-4 rounded-xl border p-6"
    >
      <div className="space-y-1">
        <h1 className="font-semibold text-xl">Sign in</h1>
        <p className="text-muted-foreground text-sm">
          Enter the password to continue.
        </p>
      </div>

      <div className="space-y-2">
        <label htmlFor="password" className="font-medium text-sm">
          Password
        </label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={login.isPending}
        />
      </div>

      {login.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {errorMessage(login.error)}
        </p>
      ) : null}

      <Button type="submit" disabled={login.isPending} className="w-full">
        {login.isPending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

/**
 * The mutation error is the parsed response body, so a rejected password
 * arrives as `{ detail }` while a dead server arrives as a thrown fetch
 * error. Distinguish them: "incorrect password" is misleading advice when the
 * API is simply unreachable.
 */
function errorMessage(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    return "Incorrect password.";
  }
  return "Could not reach the server. Try again.";
}
