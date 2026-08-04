"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { $api } from "@/lib/api/client";

/**
 * Client-side gate for pages that need a session.
 *
 * This is a UX affordance, not the security boundary — the API rejects
 * unauthenticated calls itself (401 from the session guard). It exists so a
 * logged-out visitor lands on /login instead of a page full of failed
 * requests.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data, isPending, error } = $api.useQuery(
    "get",
    "/api/v1/auth/session",
  );

  const authenticated = data?.authenticated;

  useEffect(() => {
    if (authenticated === false) {
      router.replace("/login");
    }
  }, [authenticated, router]);

  if (isPending) {
    return <p className="p-8 text-muted-foreground">Loading…</p>;
  }
  if (error) {
    // Redirecting on an unreachable API would bounce the user to a login page
    // that cannot work either. Say what happened instead.
    return (
      <p className="p-8 text-destructive">
        Could not verify your session. Is the API reachable?
      </p>
    );
  }
  if (!data.authenticated) {
    return null;
  }

  return <>{children}</>;
}
