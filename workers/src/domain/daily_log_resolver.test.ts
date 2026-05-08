import assert from "node:assert/strict";
import { chooseCanonicalDailyLogPage, extractDailyLogDateFromTitle } from "./daily_log_resolver";

assert.equal(extractDailyLogDateFromTitle("Daily Log｜2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log | 2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log 2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log｜ 2026-05-07"), "2026-05-07");

const pageA = {
  id: "359dec27-c9aa-819d-adf0-eb09fa03f36d",
  created_time: "2026-05-07T00:00:00.000Z",
  last_edited_time: "2026-05-07T08:00:00.000Z",
  properties: {
    Date: { date: { start: "2026-05-07" } },
    "Target Date": { date: { start: "2026-05-07" } },
    Diary: { rich_text: [{ plain_text: "x" }] },
  },
};
const pageB = {
  id: "359dec27-c9aa-8157-af19-cb259a0a1b4e",
  created_time: "2026-05-07T00:10:00.000Z",
  last_edited_time: "2026-05-07T09:00:00.000Z",
  properties: {
    "Target Date": { date: { start: "2026-05-07" } },
    "Location summary (GPT)": { rich_text: [{ plain_text: "loc" }] },
    "Meal Photos": { files: [{ name: "p", external: { url: "https://example.com/p.jpg" } }] },
  },
};

assert.equal(chooseCanonicalDailyLogPage([pageA as any, pageB as any], "2026-05-07")?.id, pageA.id);
console.log("daily_log_resolver.test.ts: ok");
