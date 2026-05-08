import assert from "node:assert/strict";
import { buildDuplicateMergePatch, chooseCanonicalDailyLogPage, extractDailyLogDateFromTitle, toNotionUpdateProperty } from "./daily_log_resolver";

assert.equal(extractDailyLogDateFromTitle("Daily Log｜2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log | 2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log 2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log｜ 2026-05-07"), "2026-05-07");

const pageA: any = { id: "359dec27-c9aa-819d-adf0-eb09fa03f36d", properties: { Date: { date: { start: "2026-05-07" } }, "Target Date": { date: { start: "2026-05-07" } }, Diary: { rich_text: [{ plain_text: "diary" }] }, "Today advice": { rich_text: [{ plain_text: "advice" }] }, Weather: { rich_text: [{ plain_text: "weather" }] }, "Location summary (GPT)": { rich_text: [] }, "Meal Photos": { files: [] } } };
const pageB: any = { id: "359dec27-c9aa-8157-af19-cb259a0a1b4e", properties: { "Target Date": { date: { start: "2026-05-07" } }, "Location summary (GPT)": { rich_text: [{ plain_text: "loc" }] }, "Meal Photos": { files: [{ name: "a", external: { url: "https://dropbox.com/s/1.jpg?dl=0" } }, { name: "b", external: { url: "https://dropbox.com/s/1.jpg?raw=1" } }] }, Mood: { select: { name: "Good" } }, Notes: { rich_text: [{ plain_text: "note" }] } } };

assert.equal(chooseCanonicalDailyLogPage([pageA, pageB], "2026-05-07")?.id, pageA.id);
const patch = buildDuplicateMergePatch(pageA, [pageB]);
assert.equal(patch.hasChanges, true);
assert.ok(patch.mergedFields.includes("Location summary (GPT)"));
assert.ok(patch.mergedFields.includes("Meal Photos"));
assert.ok(patch.mergedFields.includes("Mood"));
assert.ok(patch.mergedFields.includes("Notes"));
assert.equal((patch.properties["Meal Photos"].files ?? []).length, 1);
assert.equal(pageA.properties.Diary.rich_text[0].plain_text, "diary");
console.log("daily_log_resolver.test.ts: ok");

assert.deepEqual(toNotionUpdateProperty({ id:"x", type:"rich_text", rich_text:[{plain_text:"a"}] }), { rich_text:[{plain_text:"a"}] });
assert.deepEqual(toNotionUpdateProperty({ id:"x", type:"select", select:{name:"Good"} }), { select:{name:"Good"} });
assert.equal(toNotionUpdateProperty({ id:"x", type:"formula", formula:{} }), null);
