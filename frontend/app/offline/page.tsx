import Link from "next/link";

export const metadata = {
  title: "Offline — arc",
};

/**
 * What the installed app shows for a page it has never opened online, when
 * there is no network to fetch it.
 *
 * The service worker precaches this page and serves it in place of the
 * browser's own error (`public/sw.js`). Outside `(app)`, so it needs neither
 * a session nor the athlete's clock — both are requests this page exists
 * because it cannot make. It names the remedy beside the problem: Today is
 * always kept for offline use.
 */
export default function OfflinePage() {
  return (
    <main className="flex flex-1 items-center justify-center bg-canvas p-6">
      <div className="flex max-w-sm flex-col gap-3">
        <h1 className="font-semibold text-ink text-lg">You are offline</h1>
        <p className="text-ink-secondary text-sm">
          This page has not been opened on this device yet, so it is not saved
          for use without a network. It will be once you open it online.
        </p>
        <Link href="/today" className="text-accent text-sm hover:underline">
          Open Today, which is always saved
        </Link>
      </div>
    </main>
  );
}
