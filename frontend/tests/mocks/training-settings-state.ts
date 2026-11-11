import type { components } from "@/generated/api/schema";

type Settings = components["schemas"]["TrainingSettingsRead"];
type Update = components["schemas"]["TrainingSettingsUpdate"];
type Answer = components["schemas"]["LiftedBefore"];

/** What `GET /settings/training` answers on a database nobody has touched. */
export const FRESH_TRAINING_SETTINGS: Settings = {
  mode: "simple",
  plain_logbook: false,
  ask_frequency: "always",
  units: "kg",
  locale: "en",
  high_contrast: false,
  before_you_start_seen: false,
  first_run_seen: false,
  lifted_before: null,
  no_max_efforts: false,
};

/**
 * The default for every component test that is not *about* the first run:
 * an athlete who answered "regularly" and closed the before-you-start screen
 * — produced by running the same changes the real service would, so the mode
 * is the one that answer earns.
 */
function answeredRegularly(): Settings {
  return withChanges(
    withChanges(FRESH_TRAINING_SETTINGS, { lifted_before: "regularly" }),
    { before_you_start_seen: true },
  );
}

let settings: Settings = answeredRegularly();

/** `mode_for_answer` in `app/domain/training_mode.py`. */
function modeForAnswer(answer: Answer | null): Settings["mode"] {
  return answer === null || answer === "never" ? "simple" : "standard";
}

/**
 * `with_changes` in `app/domain/training_mode.py`, run over the mock's state:
 * an answer marks the question seen and, when it is new, sets the mode —
 * unless the same change names a mode or Full control was chosen.
 */
function withChanges(current: Settings, changes: Update): Settings {
  const derived: Partial<Settings> = {};
  const answer = changes.lifted_before;
  if (answer !== undefined) {
    derived.first_run_seen = true;
    if (
      answer !== current.lifted_before &&
      changes.mode === undefined &&
      current.mode !== "full"
    ) {
      derived.mode = modeForAnswer(answer);
    }
  }
  return { ...current, ...derived, ...changes };
}

/** The stored settings, as `GET /settings/training` would answer them. */
export function trainingSettings(): Settings {
  return settings;
}

/** `PATCH /settings/training`: apply what was sent and answer the result. */
export function patchTrainingSettings(changes: Update): Settings {
  settings = withChanges(settings, changes);
  return settings;
}

/** Start the test from a fresh install: nothing answered, nothing seen. */
export function seedFreshTrainingSettings(): void {
  settings = FRESH_TRAINING_SETTINGS;
}

export function resetTrainingSettingsState(): void {
  settings = answeredRegularly();
}
