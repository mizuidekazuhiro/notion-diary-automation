import assert from "node:assert/strict";

let moduleUnderTest: any;
try {
  moduleUnderTest = await import("./index");
} catch (error) {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : JSON.stringify(error);
  throw new Error(`failed_to_import_index_module ${message}`);
}

const testExports = moduleUnderTest?.__test__;
assert.ok(testExports, "__test__ export is missing");

const {
  resolveLocationSummaryFields,
  normalizeFilesFromProperty,
  getFileUrlsFromProperty,
  buildDailyLogUpsertProperties,
  getMealPhotosFilesCount,
  buildDailyLogUpsertDiagnostics,
} = testExports;

(() => {
  const props = {
    "Location summary (GPT)": { rich_text: [{ plain_text: "GPT要約" }] },
    "Location summary": { rich_text: [{ plain_text: "Legacy要約" }] },
    "location_summary": { rich_text: [{ plain_text: "payload要約" }] },
  };
  const resolved = resolveLocationSummaryFields(props, {} as any);
  assert.equal(resolved.locationSummaryGpt, "GPT要約");
  assert.equal(resolved.locationSummary, "GPT要約");
  assert.equal(resolved.locationSummarySource, "location_summary_gpt");
})();

(() => {
  const props = {
    "Location summary": { rich_text: [{ plain_text: "Legacy要約" }] },
    "location_summary": { rich_text: [{ plain_text: "payload要約" }] },
  };
  const resolved = resolveLocationSummaryFields(props, {} as any);
  assert.equal(resolved.locationSummary, "Legacy要約");
  assert.equal(resolved.locationSummarySource, "location_summary_legacy");
})();

(() => {
  assert.equal(typeof buildDailyLogUpsertProperties, "function");
  assert.equal(typeof getMealPhotosFilesCount, "function");
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
  assert.equal(typeof buildDailyLogUpsertDiagnostics, "function");
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
  const filesProp = {
    files: [
      { name: "external-photo", type: "external", external: { url: "https://example.com/a.jpg" } },
      { name: "notion-file", type: "file", file: { url: "https://www.notion.so/image/abc?signed=1" } },
      { name: "broken-only-name" },
      { type: "external", external: {} },
    ],
  };
  const normalized = normalizeFilesFromProperty(filesProp as any);
  assert.equal(normalized.length, 2);
  const urls = getFileUrlsFromProperty(filesProp as any);
  assert.deepEqual(urls, ["https://example.com/a.jpg", "https://www.notion.so/image/abc?signed=1"]);
})();

console.log("index.daily_log_read.test.ts: ok");
