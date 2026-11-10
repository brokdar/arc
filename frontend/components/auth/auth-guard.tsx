"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { $api } from "@/lib/api/client";
import {
  forgetContact,
  isUnreachable,
  lastContact,
  rememberContact,
} from "@/lib/last-contact";

/**
 * Client-side gate for pages that need a session.
 *
 * This is a UX affordance, not the security boundary — the API rejects
 * unauthenticated calls itself (401 from the session guard). It exists so a
 * logged-out visitor lands on /login instead of a page full of failed
 * requests.
 *
 * With no server to ask — the installed app opened in a gym with no signal —
 * it opens the app on a phone whose last online answer was "signed in"
 * (`lib/last-contact.ts`), because the cached shell is all an offline phone
 * can show and the API refuses every call without the cookie anyway. A
 * server that answers, with anything, is believed: "not signed in" forgets
 * the phone's sign-in and an error status is still reported as one.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data, isPending, error, fetchStatus } = $api.useQuery(
    "get",
    "/api/v1/auth/session",
  );

  const authenticated = data?.authenticated;

  useEffect(() => {
    if (authenticated === true) {
      rememberContact(new Date());
    } else if (authenticated === false) {
      forgetContact();
      router.replace("/login");
    }
  }, [authenticated, router]);

  // `paused` is react-query holding the request because the browser says it
  // is offline; a rejected fetch is the server out of reach from here.
  const unreachable =
    (isPending && fetchStatus === "paused") ||
    (error !== null && isUnreachable(error));
  if (unreachable && lastContact() !== null) {
    return <>{children}</>;
  }

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
