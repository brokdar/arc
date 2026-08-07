import { SessionDetail } from "@/components/sessions/session-detail";

export const metadata = {
  title: "Session — arc",
};

/**
 * One completed session. `params` is a promise in this Next major, so the
 * route awaits it before handing the id to the client component that fetches.
 */
export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SessionDetail sessionId={id} />;
}
