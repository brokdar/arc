import { WorkoutEditor } from "@/components/workouts/workout-editor";

export const metadata = {
  title: "Workout — arc",
};

/**
 * The creator and the editor share this route: `/workouts/new` is the literal
 * `new`, and anything else is a workout id. One segment rather than a static
 * `new/` beside a dynamic `[id]/`, because they are the same form — see
 * `WorkoutEditor` — and two routes would be two places to keep it wired.
 */
export default async function WorkoutPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <WorkoutEditor workoutId={id === "new" ? null : id} />;
}
