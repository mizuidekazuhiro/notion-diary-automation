import assert from "node:assert/strict";
import { mergeIngestSourceStatus } from "./source_status";
const before = JSON.stringify({ study: { status: "confirmed_zero" } });
function from(status: string, available_fields: string[] = []) {
  return JSON.parse(mergeIngestSourceStatus(JSON.stringify({raw:{health:{status,available_fields}}}), before)!);
}
assert.equal(from("failed").sleep.status, "query_failed");
assert.equal(from("no_data").sleep.status, "missing");
assert.equal(from("degraded", ["weight"]).sleep.status, "missing");
assert.equal(from("degraded", ["sleep_duration_min"]).sleep.status, "observed");
assert.equal(from("degraded", ["kcal"]).nutrition.status, "observed");
assert.equal(from("failed").study.status, "confirmed_zero");
assert.equal(from("stale", ["sleep_duration_min", "kcal"]).sleep.status, "stale");
assert.equal(from("stale", ["sleep_duration_min", "kcal"]).nutrition.status, "stale");
assert.equal(mergeIngestSourceStatus(undefined, before), null);
const timestampOnly = JSON.parse(mergeIngestSourceStatus(JSON.stringify({ raw: { health: {
  status: "no_data", available_fields: [], payload: { health_quality: { source_availability: { sleep: { status: "observed" } } } },
} } }), before)!);
assert.equal(timestampOnly.sleep.status, "observed");
assert.equal(timestampOnly.nutrition.status, "missing");
