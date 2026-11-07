import assert from "node:assert/strict";
import worker, { __test__ } from "./index";

const target = "2026-11-05";
const env = { WORKERS_BEARER_TOKEN: "worker", NOTION_TOKEN: "notion", DAILY_LOG_DB_ID: "timestamp-daily", HEALTH_DB_ID: "timestamp-health" } as any;
const schema: Record<string, any> = {};
for (const prop of __test__.buildDailyLogProperties(env)) schema[prop.name] = { type: prop.type };
schema["Data Status JSON"] = { type: "rich_text" };
const start = "2026-11-05T23:00:00+09:00", end = "2026-11-06T07:00:00+09:00";
let written: any = null;
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {
  const path = String(url);
  if (path.endsWith("timestamp-health/query")) return Response.json({ results: [{ id: "health-page", properties: {
    Date: {date:{start:target}}, "Sleep Start": {date:{start}}, "Sleep End": {date:{start:end}},
  } }], has_more: false });
  if (path.endsWith("timestamp-daily/query")) return Response.json({ results: [{ id: "daily-page", properties: {Date:{date:{start:target}},"Target Date":{date:{start:target}}} }],has_more:false });
  if (path.endsWith("/pages/daily-page")) { written=JSON.parse(String(init?.body)); return Response.json({id:"daily-page"}); }
  return Response.json({properties: path.endsWith("timestamp-health") ? {Date:{type:"date"},"Sleep Start":{type:"date"},"Sleep End":{type:"date"}} : schema});
};
try {
  const response=await worker.fetch(new Request("https://worker.test/execute/api/daily_log/ingest_health", {
    method:"POST",headers:{authorization:"Bearer worker","content-type":"application/json"},body:JSON.stringify({target_date:target}),
  }),env);
  const data=await response.json() as any;
  assert.equal(response.status,200);
  assert.equal(data.updated,true);
  assert.equal(data.health_quality.completeness,0);
  assert.equal(data.health_quality.status,"no_data");
  assert.equal(data.health_quality.source_availability.sleep.status,"observed");
  assert.equal(written.properties["Sleep Start"].date.start,start);
  assert.equal(written.properties["Sleep End"].date.start,end);
} finally {globalThis.fetch=originalFetch;}
