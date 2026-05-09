import assert from "node:assert/strict";
import { __test__ } from "./index";

const { resolveLocationSummaryFields, normalizeFilesFromProperty, getFileUrlsFromProperty, buildDailyLogUpsertProperties, getMealPhotosFilesCount, buildDailyLogUpsertDiagnostics } = __test__;

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
  const props = {
    "location_summary": { rich_text: [{ plain_text: "payload要約" }] },
  };
  const resolved = resolveLocationSummaryFields(props, {} as any);
  assert.equal(resolved.locationSummary, "payload要約");
  assert.equal(resolved.locationSummarySource, "location_summary_payload");
  assert.equal(resolved.locationSummaryLegacy, null);
  assert.equal(resolved.locationSummaryPayload, "payload要約");
})();

(() => {
  const props = {
    "Custom Location Summary": { rich_text: [{ plain_text: "Custom要約" }] },
  };
  const resolved = resolveLocationSummaryFields(props, {
    DAILY_LOG_LOCATION_SUMMARY_PROP: "Custom Location Summary",
  } as any);
  assert.equal(resolved.locationSummary, "Custom要約");
  assert.equal(resolved.locationSummarySource, "location_summary_gpt");
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

(() => {
  assert.deepEqual(normalizeFilesFromProperty(undefined), []);
  assert.deepEqual(getFileUrlsFromProperty(undefined), []);
  assert.deepEqual(normalizeFilesFromProperty({ files: [] } as any), []);
})();

console.log("index.daily_log_read.test.ts: ok");

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
  assert.deepEqual(properties["Activity Summary"].rich_text[0].text.content, "summary");
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
