export function formatMealSummary(
  protein: number | null,
  fat: number | null,
  carb: number | null,
  kcal: number | null,
  weight: number | null,
): string {
  const formatMacro = (value: number | null) =>
    typeof value === "number" ? `${Math.round(value)}` : "—";
  const formatWeight = (value: number | null) =>
    typeof value === "number" ? (Math.round(value * 10) / 10).toFixed(1) : "—";

  return `P: ${formatMacro(protein)}g / F: ${formatMacro(
    fat,
  )}g / C: ${formatMacro(carb)}g | ${formatMacro(kcal)} kcal | ${formatWeight(
    weight,
  )} kg`;
}
