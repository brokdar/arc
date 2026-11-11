"use client";

import Link from "next/link";
import { useId } from "react";

import { $api } from "@/lib/api/client";
import { useI18n } from "@/lib/i18n/provider";
import { kgToLb } from "@/lib/strength";

/**
 * The finish summary's new personal bests: one line per set no earlier set
 * of the exercise beat on both weight and reps ("New best: 8 reps at
 * 100 kg on Romanian deadlift").
 *
 * The server decides what a best is (`app.domain.strength_records`); the
 * phone only prints it. An estimated-max change is never a best, so nothing
 * here ever mentions one.
 *
 * Renders **nothing** while the answer is pending, when the session set no
 * best, and when the server has not received the session yet (offline, a
 * 404): a "no bests today" line would be a dead end with no action beside
 * it, and a spinner on the gym floor would wait on a signal that may never
 * come.
 */
export function FinishSummary({
  sessionClientId,
  units = "kg",
}: {
  /** The id the phone minted at Start — the server's id is never sent back. */
  sessionClientId: string;
  units?: "kg" | "lb";
}) {
  const { t, rich, number, weight } = useI18n();
  const title = useId();
  const { data } = $api.useQuery("get", "/api/v1/personal-bests", {
    params: { query: { session_client_id: sessionClientId } },
  });
  const bests = data?.bests ?? [];
  if (bests.length === 0) return null;

  const written = (kg: number) =>
    units === "lb"
      ? weight(Math.round(kgToLb(kg) * 10) / 10, "lb")
      : weight(kg);

  return (
    <section aria-labelledby={title} className="flex flex-col gap-2 text-left">
      <h2 id={title} className="font-semibold text-lg">
        {t("records.bestsTitle")}
      </h2>
      <ul className="flex flex-col gap-1">
        {bests.map((best) => (
          <li
            key={`${best.exercise_id ?? best.exercise_name}:${best.load_kg}:${best.reps}`}
            className="text-ink"
          >
            {rich("records.newBest", {
              reps: number(best.reps),
              weight: written(best.load_kg),
              // A catalogue lift links to its history; free text has none.
              exercise: best.exercise_id ? (
                <Link
                  href={`/exercises/${best.exercise_id}`}
                  className="underline underline-offset-2"
                >
                  {best.exercise_name}
                </Link>
              ) : (
                best.exercise_name
              ),
            })}
          </li>
        ))}
      </ul>
    </section>
  );
}
