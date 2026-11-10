import { AuthGuard } from "@/components/auth/auth-guard";
import { AppShell } from "@/components/shell/app-shell";
import { ClockProvider } from "@/lib/clock";
import { I18nProvider } from "@/lib/i18n/provider";

/**
 * Every signed-in page of the app.
 *
 * The route group `(app)` contributes nothing to the URL — it exists so the
 * guard and the shell are declared once. `/login` sits outside it, which is
 * what keeps a logged-out visitor from being wrapped in a nav they cannot use.
 *
 * The clock is read here, inside the guard and above everything: `/clock`
 * needs a session, and every page below this one names a day. Reading it once
 * at the top is what makes there be **one** — a component deriving its own
 * would be the browser's clock again, which is the whole of issue #62.
 *
 * The language is read here too, for the same reason and from the same
 * place: `locale` in the training settings needs a session, and every page
 * below draws its text from the catalogues (`lib/i18n`).
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <I18nProvider>
        <ClockProvider>
          <AppShell>{children}</AppShell>
        </ClockProvider>
      </I18nProvider>
    </AuthGuard>
  );
}
