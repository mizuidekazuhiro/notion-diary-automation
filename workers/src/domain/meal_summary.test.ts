import assert from "node:assert/strict";

import { formatMealSummary } from "./meal_summary";

const cases: Array<{
  input: [number | null, number | null, number | null, number | null, number | null];
  expected: string;
}> = [
  {
    input: [120, 60, 180, 2100, 72.44],
    expected: "P: 120g / F: 60g / C: 180g | 2100 kcal | 72.4 kg",
  },
  {
    input: [null, 10.2, 20.6, null, null],
    expected: "P: —g / F: 10g / C: 21g | — kcal | — kg",
  },
  {
    input: [99.5, 0.4, 0.49, 1999.9, 70],
    expected: "P: 100g / F: 0g / C: 0g | 2000 kcal | 70.0 kg",
  },
];

for (const { input, expected } of cases) {
  const [protein, fat, carb, kcal, weight] = input;
  assert.equal(formatMealSummary(protein, fat, carb, kcal, weight), expected);
}

console.log("meal_summary tests passed");
