import { AuthGuard } from "@/components/auth/auth-guard";

export default function Home() {
  return (
    <AuthGuard>
      <main className="flex flex-1 flex-col items-center justify-center gap-6 p-8">
        <h1 className="font-semibold text-3xl">arc</h1>
        <p className="max-w-md text-center text-muted-foreground">
          Training application for one athlete. The plan, calendar and session
          views arrive with WP-3; the athlete profile, anchors and zones are
          already served by the API.
        </p>
      </main>
    </AuthGuard>
  );
}
