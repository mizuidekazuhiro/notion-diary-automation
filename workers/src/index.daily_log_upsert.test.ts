import assert from "node:assert/strict";
import { __test__ } from "./index";

const {
  buildHealthIngestQueryBody,
  buildLatestValidHealthQueryBody,
  buildDailyLogProperties,
  buildDailyLogUpsertProperties,
  getHealthPropertyNames,
  getMealPhotosFilesCount,
  buildDailyLogUpsertDiagnostics,
  sanitizeMealPhotosPatchProperties,
  parseStudyPayload,
  applyStudyUpdateProperties,
} = __test__;

(() => {
  const healthPropertyNames = getHealthPropertyNames({
    HEALTH_DATE_PROPERTY_NAME: "Date",
    HEALTH_SOURCE_PROPERTY_NAME: "Source",
    HEALTH_SOURCE_VALUE: "healthkit",
  } as any);
  const body = buildHealthIngestQueryBody("2026-06-24", healthPropertyNames);

  assert.deepEqual(body, {
    page_size: 5,
    filter: { property: "Date", date: { equals: "2026-06-24" } },
    sorts: [{ timestamp: "created_time", direction: "descending" }],
  });
  assert.equal(JSON.stringify(body).includes("Source"), false);
  assert.equal(JSON.stringify(body).includes("healthkit"), false);
})();

(() => {
  const healthPropertyNames = getHealthPropertyNames({
    HEALTH_DATE_PROPERTY_NAME: "Date",
  } as any);
  const body = buildLatestValidHealthQueryBody("2026-08-10", healthPropertyNames);

  assert.deepEqual(body, {
    page_size: 50,
    filter: { property: "Date", date: { on_or_before: "2026-08-10" } },
    sorts: [{ property: "Date", direction: "descending" }],
  });
})();

(() => {
  const healthPropertyNames = getHealthPropertyNames({
    HEALTH_DATE_PROPERTY_NAME: "Date",
    HEALTH_SOURCE_PROPERTY_NAME: "Source",
  } as any);
  const body = buildHealthIngestQueryBody("2026-06-24", healthPropertyNames);
  const sourceName = "Mitsui_iPhone";
  const healthPage = {
    properties: {
      Date: { date: { start: "2026-06-24" } },
      Source: { select: { name: sourceName } },
      sleep_duration_min: { number: 297 },
    },
  };

  assert.deepEqual(body.filter, { property: "Date", date: { equals: "2026-06-24" } });
  assert.equal((healthPage.properties.Source as any).select.name, "Mitsui_iPhone");
  assert.equal((healthPage.properties.sleep_duration_min as any).number, 297);
})();

(() => {
  const schema = buildDailyLogProperties({} as any);
  const required = new Map(schema.map((item: { name: string; type: string }) => [item.name, item.type]));
  assert.equal(required.get("Mail Input Hash"), "rich_text");
  assert.equal(required.get("Mail Sent At"), "date");
  assert.equal(required.get("Mail Version"), "number");
  assert.equal(required.get("Study Minutes"), "number");
  assert.equal(required.get("Study Sessions"), "number");
  assert.equal(required.get("Study Last Used At"), "date");
  assert.equal(required.get("Mail Input Snapshot"), "rich_text");
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
