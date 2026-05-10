import assert from "node:assert/strict";
import { __test__ } from "./index";

const {
  buildDailyLogProperties,
  buildDailyLogUpsertProperties,
  getMealPhotosFilesCount,
  buildDailyLogUpsertDiagnostics,
  sanitizeMealPhotosPatchProperties,
  parseStudyPayload,
  applyStudyUpdateProperties,
} = __test__;

(() => {
  const schema = buildDailyLogProperties({} as any);
  const required = new Map(schema.map((item: { name: string; type: string }) => [item.name, item.type]));
  assert.equal(required.get("Mail Input Hash"), "rich_text");
  assert.equal(required.get("Mail Sent At"), "date");
  assert.equal(required.get("Mail Version"), "number");
  assert.equal(required.get("Study Minutes"), "number");
  assert.equal(required.get("Study Sessions"), "number");
  assert.equal(required.get("Study Last Used At"), "date");
  assert.equal(required.has("Mail Input Snapshot"), false);
})();

(() => {
  const parsed = parseStudyPayload({ study_minutes: 0, study_sessions: 0, study_last_used_at: "2026-05-10T09:00:00+09:00" });
  const updates: Record<string, unknown> = {};
  const schema = {
    "Study Minutes": { type: "number" },
    "Study Sessions": { type: "number" },
    "Study Last Used At": { type: "date" },
  };
  applyStudyUpdateProperties(updates as any, schema as any, parsed);
  assert.equal((updates["Study Minutes"] as any).number, 0);
  assert.equal((updates["Study Sessions"] as any).number, 0);
  assert.equal((updates["Study Last Used At"] as any).date.start, "2026-05-10T09:00:00+09:00");
})();

(() => {
  const properties = buildDailyLogUpsertProperties({
    title: "Daily Log｜2026-05-08",
    targetDate: "2026-05-08",
    summaryText: "summary",
    mailId: "25597151669",
    source: "automation",
  });
  assert.equal("Meal Photos" in properties, false);
  assert.equal(getMealPhotosFilesCount(properties), 0);
})();

(() => {
  const properties = buildDailyLogUpsertProperties({
    title: "Daily Log｜2026-05-08",
    targetDate: "2026-05-08",
    summaryText: "summary",
    mailId: "25597151669",
    source: "automation",
  });
  const diagnostics = buildDailyLogUpsertDiagnostics({
    targetDate: "2026-05-08",
    pageId: "page-override",
    canonicalPageId: "canonical-page",
    duplicateDetected: true,
    duplicateMergeCompleted: false,
    properties,
  });
  assert.equal(diagnostics.page_id_overrode_canonical, true);
  assert.equal(diagnostics.resolved_update_page_id, "page-override");
  assert.equal(diagnostics.patch_includes_meal_photos, false);
})();

(() => {
  const source = {
    "Meal Photos": { files: [] },
    "Activity Summary": { rich_text: [{ text: { content: "x" } }] },
  };
  const out = sanitizeMealPhotosPatchProperties(source as any);
  assert.equal(out.removedEmptyMealPhotos, true);
  assert.equal("Meal Photos" in out.sanitizedProperties, false);
  assert.equal("Meal Photos" in source, true);
})();

(() => {
  const source = {
    "Meal Photos": { files: [{ name: "a", external: { url: "https://example.com/a.jpg" } }] },
  };
  const out = sanitizeMealPhotosPatchProperties(source as any);
  assert.equal(out.removedEmptyMealPhotos, false);
  assert.equal("Meal Photos" in out.sanitizedProperties, true);
})();

(() => {
  const source = { "Activity Summary": { rich_text: [{ text: { content: "x" } }] } };
  const out = sanitizeMealPhotosPatchProperties(source as any);
  assert.equal(out.removedEmptyMealPhotos, false);
  assert.deepEqual(out.sanitizedProperties, source);
})();

console.log("index.daily_log_upsert.test.ts: ok");
