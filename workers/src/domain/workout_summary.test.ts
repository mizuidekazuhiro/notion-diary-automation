import assert from "node:assert/strict";
import { validateWorkoutSummary } from "./workout_summary";

const zero = { target_date: "2026-11-05", workout_done: false, sessions: 0, duration_min: 0, sets: 0, volume_kg: 0, calories: 0 };
assert.equal(validateWorkoutSummary(zero, "2026-11-05"), true);
for (const payload of [{}, { ...zero, target_date: "2026-11-04" }, { ...zero, sessions: "0" },
                       { ...zero, calories: NaN }, { ...zero, workout_done: true }, { ...zero, duration_min: -1 }]) {
  assert.equal(validateWorkoutSummary(payload, "2026-11-05"), false);
}
assert.equal(validateWorkoutSummary({ ...zero, workout_done: true, sessions: 1, duration_min: 30 }, "2026-11-05"), true);
