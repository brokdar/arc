import { LoggingScreen } from "@/components/strength/logging-screen";

export const metadata = {
  title: "Train — arc",
};

/**
 * Logging one planned strength session in the gym. The id is the planned
 * session's; the screen reads everything else from the packet stored on the
 * phone, so the route works with no signal. `params` is a promise in this
 * Next major.
 */
export default async function TrainPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <LoggingScreen plannedSessionId={id} />;
}
