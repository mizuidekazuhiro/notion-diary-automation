import assert from "node:assert/strict";

import {
  buildMealPhotoUpdateProperties,
  buildPhotoOnlyUpdateProperties,
  collectMealPhotosFromHealthPages,
  resolveIngestTargetDate,
} from "./daily_log_ingest";

const targetDate = resolveIngestTargetDate({ date: "2026-01-01" });
assert.equal(targetDate.ok, true);
if (targetDate.ok) {
  assert.equal(targetDate.targetDate, "2026-01-01");
}

const targetDateFromLegacy = resolveIngestTargetDate({ target_date: "2026-01-02" });
assert.equal(targetDateFromLegacy.ok, true);
if (targetDateFromLegacy.ok) {
  assert.equal(targetDateFromLegacy.targetDate, "2026-01-02");
}

const invalidDate = resolveIngestTargetDate({ date: "2026/01/01" });
assert.equal(invalidDate.ok, false);

const photos = collectMealPhotosFromHealthPages(
  [
    {
      properties: {
        "Meal Photos": {
          files: [
            { name: "a", type: "external", external: { url: "https://a.example" } },
            { name: "b", type: "external", external: { url: "https://b.example" } },
          ],
        },
      },
    },
    {
      properties: {
        "Meal Photos": {
          files: [
            { name: "a2", type: "external", external: { url: "https://a.example" } },
          ],
        },
      },
    },
  ],
  "Meal Photos",
  (property) => (Array.isArray(property?.files) ? property.files : []),
);
assert.equal(photos.length, 2);

const updatePayload = buildPhotoOnlyUpdateProperties("Meal Photos", { files: photos });
assert.deepEqual(Object.keys(updatePayload), ["Meal Photos"]);
assert.equal(updatePayload.Protein, undefined);
assert.equal(updatePayload.Fat, undefined);
assert.equal(updatePayload.Carb, undefined);
assert.equal(updatePayload.Kcal, undefined);
assert.equal(updatePayload.Weight, undefined);

const noIncomingUpdatePayload = buildMealPhotoUpdateProperties(
  "Meal Photos",
  [{ name: "existing", type: "external", external: { url: "https://existing.example" } }],
  [],
);
assert.equal("Meal Photos" in noIncomingUpdatePayload, false);

const keepExistingAndAddIncomingPayload = buildMealPhotoUpdateProperties(
  "Meal Photos",
  [{ name: "existing", type: "external", external: { url: "https://existing.example" } }],
  [{ name: "new", type: "external", external: { url: "https://new.example" } }],
);
assert.equal("Meal Photos" in keepExistingAndAddIncomingPayload, true);
assert.equal(keepExistingAndAddIncomingPayload["Meal Photos"].files.length, 2);
assert.equal(
  keepExistingAndAddIncomingPayload["Meal Photos"].files[0].external.url,
  "https://existing.example",
);

console.log("daily_log_ingest tests passed");
