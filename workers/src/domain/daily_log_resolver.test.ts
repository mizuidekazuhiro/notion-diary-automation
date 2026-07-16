import assert from "node:assert/strict";
import { analyzeDailyLogPages, buildCanonicalDailyLogTitle, buildDuplicateMergePatch, chooseCanonicalDailyLogPage, extractDailyLogDateFromTitle, isPageMatchedByDateOrTitle, normalizeDailyLogTitle, resolveDailyLogOfficialDate } from "./daily_log_resolver";

assert.equal(extractDailyLogDateFromTitle("Daily Log｜2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log | 2026-05-07"), "2026-05-07");
assert.equal(extractDailyLogDateFromTitle("Daily Log ❘ 2026-05-07"), "2026-05-07");
assert.equal(buildCanonicalDailyLogTitle("2026-05-07"), "Daily Log｜2026-05-07");
assert.equal(normalizeDailyLogTitle("Daily Log | 2026-05-07"), "Daily Log｜2026-05-07");

const title = (s:string)=>({title:[{plain_text:s}]});
const pDateTitleMismatch:any={id:"x",properties:{"名前":title("Daily Log｜2022-02-22"),Date:{date:{start:"2026-02-22"}},"Target Date":{date:{start:"2026-02-22"}}}};
assert.equal(resolveDailyLogOfficialDate(pDateTitleMismatch).date,"2026-02-22");
assert.equal(isPageMatchedByDateOrTitle(pDateTitleMismatch,"2022-02-22"),false);
assert.equal(isPageMatchedByDateOrTitle(pDateTitleMismatch,"2026-02-22"),true);
const dateOnly:any={id:"d",properties:{Date:{date:{start:"2026-07-15"}},"名前":title("Daily Log｜2022-07-15")}};
assert.equal(resolveDailyLogOfficialDate(dateOnly).date,"2026-07-15");
const targetOnly:any={id:"t",properties:{"Target Date":{date:{start:"2026-07-15"}}}};
assert.equal(resolveDailyLogOfficialDate(targetOnly).date,"2026-07-15");
const ambiguous:any={id:"a",properties:{Date:{date:{start:"2026-07-15"}},"Target Date":{date:{start:"2026-07-16"}}}};
assert.equal(resolveDailyLogOfficialDate(ambiguous).ambiguous,true);
assert.equal(isPageMatchedByDateOrTitle(ambiguous,"2026-07-15"),false);
assert.deepEqual(analyzeDailyLogPages([ambiguous], "2026-07-15").ambiguousPages.map((p:any)=>p.id), ["a"]);

const pageA: any = { id: "a", properties: { Date: { date: { start: "2026-05-07" } }, "Target Date": { date: { start: "2026-05-07" } }, "名前": title("Daily Log｜2026-05-07"), Diary: { rich_text: [{ plain_text: "diary" }] }, "Today advice": { rich_text: [{ plain_text: "advice" }] }, "Done Tasks": { relation: [{id:"1"}] }, "Meal Photos": { files: [{ name: "a", external: { url: "https://dropbox.com/s/1.jpg?dl=0" } }] } } };
const pageB: any = { id: "b", properties: { "Target Date": { date: { start: "2026-05-07" } }, "Done Tasks": { relation: [{id:"1"},{id:"2"}] }, "Meal Photos": { files: [{ name: "b", external: { url: "https://dropbox.com/s/1.jpg?raw=1" } }, { name: "c", external: { url: "https://example.com/c.jpg" } }] }, Mood: { select: { name: "Good" } }, Notes: { rich_text: [{ plain_text: "note" }] } } };
assert.equal(chooseCanonicalDailyLogPage([pageB,pageA],"2026-05-07")?.id,"a");
const analyzed = analyzeDailyLogPages([pageA,pageB,ambiguous], "2026-05-07");
assert.equal(analyzed.canonicalPage?.id, "a");
assert.equal(analyzed.duplicatePages.length, 1);
assert.equal(analyzed.ambiguousPages.length, 0);
const patch=buildDuplicateMergePatch(pageA,[pageB]);
assert.ok(patch.mergedFields.includes("Done Tasks"));
assert.ok(patch.mergedFields.includes("Meal Photos"));
assert.equal(patch.properties["Done Tasks"].relation.length,2);
assert.equal(patch.properties["Meal Photos"].files.length,2);
assert.equal(patch.properties.Mood.select.name,"Good");
console.log("daily_log_resolver.test.ts: ok");
