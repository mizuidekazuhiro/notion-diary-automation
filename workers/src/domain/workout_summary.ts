export function validateWorkoutSummary(value: unknown, targetDate: string): value is Record<string, any> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  if (data.target_date !== targetDate || typeof data.workout_done !== "boolean") return false;
  const numeric = ["sessions", "duration_min", "sets", "volume_kg", "calories"];
  if (numeric.some((key) => typeof data[key] !== "number" || !Number.isFinite(data[key]) || Number(data[key]) < 0)) return false;
  if (data.workout_done !== (Number(data.sessions) > 0)) return false;
  return true;
}
