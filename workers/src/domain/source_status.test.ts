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
assert.equal(mergeIngestSourceStatus(undefined, before), null);
