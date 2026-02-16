import assert from "node:assert/strict";
import {
  buildFallbackLocationSummary,
  resolveLocationWindow,
  segmentLocationLogs,
  type NormalizedLocationLog,
} from "./location_summary";

const window = resolveLocationWindow(new Date("2026-01-10T21:00:00Z"), 5);
assert.equal(window.anchorStartIso, "2026-01-10T05:00:00+09:00");
assert.equal(window.anchorEndIso, "2026-01-11T05:00:00+09:00");
assert.equal(window.diaryDate, "2026-01-10");

const noLogs = buildFallbackLocationSummary(
  "2026-01-10T05:00:00+09:00",
  "2026-01-11T05:00:00+09:00",
  [],
  0,
  [],
);
assert.match(noLogs.location_summary_text, /位置ログがありませんでした/);

const logs: NormalizedLocationLog[] = [
  {
    timeIso: "2026-01-10T06:00:00+09:00",
    timeMs: Date.parse("2026-01-10T06:00:00+09:00"),
    place: "千代田区 大手町",
    lat: 35.6879,
    lon: 139.7639,
    source: "gps",
  },
  {
    timeIso: "2026-01-10T07:00:00+09:00",
    timeMs: Date.parse("2026-01-10T07:00:00+09:00"),
    place: "千代田区 大手町",
    lat: 35.6879,
    lon: 139.7639,
    source: "gps",
  },
  {
    timeIso: "2026-01-10T08:00:00+09:00",
    timeMs: Date.parse("2026-01-10T08:00:00+09:00"),
    place: "港区 芝浦",
    lat: 35.64,
    lon: 139.75,
    source: "gps",
  },
];

const segmented = segmentLocationLogs(logs, 4, 5);
assert.equal(segmented.moveCount, 1);
assert.equal(segmented.segments.length, 2);

const summary = buildFallbackLocationSummary(
  "2026-01-10T05:00:00+09:00",
  "2026-01-11T05:00:00+09:00",
  segmented.segments,
  segmented.moveCount,
  [],
);
assert.match(summary.location_summary_text, /タイムライン:/);
assert.equal(summary.stats.move_count, 1);

console.log("location_summary tests passed");
