import assert from "node:assert/strict";
import { __test__ } from "./index";

const {
  resolveLocationSummaryFields,
  extractMailMetadataFromProperties,
  normalizeFilesFromProperty,
  getFileUrlsFromProperty,
} = __test__;

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
  const metadata = extractMailMetadataFromProperties({
    "Mail Input Hash": { rich_text: [{ plain_text: "abc123" }] },
    "Mail Sent At": { date: { start: "2026-05-10T00:00:00+09:00" } },
    "Mail Version": { number: 3 },
  } as any);
  assert.equal(metadata.mailInputHash, "abc123");
  assert.equal(metadata.mailSentAt, "2026-05-10T00:00:00+09:00");
  assert.equal(metadata.mailVersion, 3);
})();

(() => {
  assert.deepEqual(normalizeFilesFromProperty(undefined), []);
  assert.deepEqual(getFileUrlsFromProperty(undefined), []);
  assert.deepEqual(normalizeFilesFromProperty({ files: [] } as any), []);
})();

console.log("index.daily_log_read.test.ts: ok");
