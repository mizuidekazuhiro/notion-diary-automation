import assert from "node:assert/strict";
import { resolvePropertyName, resolveExactPropertyName, canUseProperty } from "./property_resolver";
import {
  createRichTextProperty, createRichTextPropertyWithLimit, createNumberProperty,
  getNumberFromProperty, getIsoStringFromProperty, getPlainTextFromRichText,
  normalizeFilesFromProperty, getFileUrlsFromProperty,
} from "./property_values";

const schema = {
  "Location summary": { type: "rich_text" },
  location_summary: { type: "rich_text" },
  "Sleep Score": { type: "number" },
};
assert.equal(resolveExactPropertyName(schema, "Location summary", "test"), "Location summary");
assert.equal(resolveExactPropertyName(schema, "LOCATION-SUMMARY", "test"), null);
assert.equal(resolvePropertyName(schema, "Location summary", "test", ["location_summary"]), null);
assert.equal(canUseProperty(schema, "sleep_score", "number", "test"), "Sleep Score");
assert.equal(canUseProperty(schema, "Sleep Score", "date", "test"), null);
assert.equal(resolvePropertyName(schema, "missing", "test"), null);

assert.deepEqual(createNumberProperty(0), { number: 0 });
assert.deepEqual(createNumberProperty(), { number: null });
assert.equal(getNumberFromProperty({ number: 0 }), 0);
assert.equal(getNumberFromProperty({ number: "0" }), null);
assert.equal(getNumberFromProperty(undefined), null);
assert.equal(getIsoStringFromProperty({ formula: { number: 0 } }), "0");
assert.equal(getIsoStringFromProperty({ formula: { boolean: false } }), "false");
assert.equal(getIsoStringFromProperty({ date: { start: " 2026-11-06 " }, rich_text: [{ plain_text: "other" }] }), "2026-11-06");
assert.equal(getPlainTextFromRichText({ rich_text: [{ text: { content: "request-only fallback" } }] }), "");

const body = "あ".repeat(1801);
const bodyParts = createRichTextProperty(body).rich_text;
assert.deepEqual(bodyParts.map(p => p.text.content.length), [1800, 1]);
assert.equal(bodyParts.map(p => p.text.content).join(""), body);
const noteParts = createRichTextPropertyWithLimit("い".repeat(2001)).rich_text;
assert.deepEqual(noteParts.map(p => p.text.content.length), [2000, 1]);
assert.equal(noteParts[0].type, "text");
assert.deepEqual(createRichTextProperty("").rich_text, []);

const files = { files: [
  { name: "external", type: "external", external: { url: "https://example.com/a" } },
  { name: "internal", type: "file", file: { url: "https://example.com/b" } },
  { type: "file" },
] };
assert.deepEqual(normalizeFilesFromProperty(files), files.files.slice(0, 2));
assert.deepEqual(getFileUrlsFromProperty(files), ["https://example.com/a", "https://example.com/b"]);
assert.deepEqual(getFileUrlsFromProperty(undefined), []);
console.log("Notion property compatibility contracts passed");
