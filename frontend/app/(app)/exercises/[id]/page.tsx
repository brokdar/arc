import { ExerciseHistory } from "@/components/strength/history/exercise-history";
import { PreferencesPanel } from "@/components/strength/swap/preferences-panel";

export const metadata = {
  title: "Exercise — arc",
};

/**
 * One catalogue exercise's history — personal bests and the estimated max —
 * and the athlete's say over swaps of it (avoid, less often, Ease back in).
 * A route rather than a sheet because it is a place worth linking to (UI
 * convention 1); the finish summary links here. `params` is a promise in
 * this Next major.
 */
export default async function ExercisePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <>
      <ExerciseHistory exerciseId={id} />
      <div className="px-4 pb-4">
        <PreferencesPanel exerciseId={id} />
      </div>
    </>
  );
}
