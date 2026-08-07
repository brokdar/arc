import { AuthGuard } from "@/components/auth/auth-guard";
import { AppShell } from "@/components/shell/app-shell";

/**
 * Every signed-in page of the app.
 *
 * The route group `(app)` contributes nothing to the URL — it exists so the
 * guard and the shell are declared once. `/login` sits outside it, which is
 * what keeps a logged-out visitor from being wrapped in a nav they cannot use.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
