import { ExerciseHistory } from "@/components/strength/history/exercise-history";

export const metadata = {
  title: "Exercise — arc",
};

/**
 * One catalogue exercise's history — personal bests and the estimated max.
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
  return <ExerciseHistory exerciseId={id} />;
}
