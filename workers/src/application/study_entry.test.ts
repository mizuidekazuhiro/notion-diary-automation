import assert from "node:assert/strict";
import vm from "node:vm";
import worker from "../index";
import { studyEntryScript } from "./study_entry";
import { parseStudySessionPayload } from "./study_session";

const context = vm.createContext({ Date, JSON, Error, Number, String, Math });
vm.runInContext(studyEntryScript.split("// UI initialization")[0], context);
const build = (date: string, time: string, minutes: number, device = "Windows PC") =>
  vm.runInContext(`buildSession(${JSON.stringify({ date, time, minutes, device })})`, context);
const midnight = build("2026-09-24", "23:45", 30);
assert.equal(midnight.started_at, "2026-09-24T14:45:00.000Z");
assert.equal(midnight.ended_at, "2026-09-24T15:15:00.000Z");
assert.equal(midnight.app, "Itojuku");
assert.equal(midnight.source, "manual");
assert.equal(midnight.session_id, build("2026-09-24", "23:45", 30).session_id);
assert.notEqual(midnight.session_id, build("2026-09-24", "23:45", 45).session_id);
assert.throws(() => build("2026-02-30", "12:00", 30));
assert.throws(() => build("2026-09-24", "25:00", 30));
for (const minutes of [0, -5, 1441, 1.5]) assert.throws(() => build("2026-09-24", "12:00", minutes));
assert.throws(() => build("2026-09-24", "12:00", 30, ""));
assert.equal(build("2026-12-31", "23:30", 60).ended_at, "2026-12-31T15:30:00.000Z");
assert.equal(parseStudySessionPayload(midnight).targetDate, "2026-09-24");
assert.equal(parseStudySessionPayload(build("2026-09-25", "03:30", 30)).targetDate, "2026-09-25");
assert.equal(parseStudySessionPayload(build("2026-09-25", "03:30", 29)).targetDate, "2026-09-24");

// Exercise the actual browser script, including pending requests and retry identity.
const elements: Record<string, any> = {};
for (const id of ["date", "time", "minutes", "device", "token", "inputs", "status", "preview", "quick", "minus", "plus"]) {
  elements[id] = { value: "", textContent: "", children: [], append(child: any) { this.children.push(child); } };
}
elements.minutes.value = "30";
elements.device.value = "Windows PC";
elements.token.value = "test-token";
const form: any = { dataset: { boundary: "4" } };
const submissions: any[] = [];
let finish: (response: any) => void = () => {};
const browser = vm.createContext({
  document: { querySelector: () => form, getElementById: (id: string) => elements[id], createElement: () => ({}) },
  fetch: (_url: string, options: any) => { submissions.push(JSON.parse(options.body)); return new Promise(resolve => { finish = resolve; }); },
});
vm.runInContext(studyEntryScript, browser);
assert.equal(elements.quick.children.length, 6);
elements.quick.children[5].onclick();
assert.equal(elements.minutes.value, 120);
elements.minus.onclick();
assert.equal(elements.minutes.value, 115);
elements.plus.onclick();
assert.equal(elements.minutes.value, 120);
const event = { preventDefault() {} };
const firstSubmit = form.onsubmit(event);
await form.onsubmit(event);
assert.equal(submissions.length, 1);
assert.equal(elements.inputs.disabled, true);
finish({ ok: false, status: 503, json: async () => ({}) });
await firstSubmit;
assert.equal(elements.inputs.disabled, false);
const retry = form.onsubmit(event);
assert.equal(submissions[0].session_id, submissions[1].session_id);
finish({ ok: true, json: async () => ({ ok: true, duplicate: true, target_date: "2026-09-24", duration_min: 120, daily_totals: { study_minutes: 120 }, daily_log_updated: true }) });
await retry;
await form.onsubmit(event);
assert.equal(submissions.length, 2);

// Mock only the Notion transport: the real Worker/API must create once and aggregate on retry.
const originalFetch = globalThis.fetch;
let saved: any = null;
let creates = 0;
let dailyUpdates = 0;
globalThis.fetch = async (input, init) => {
  const url = String(input);
  const body = JSON.parse(String(init?.body ?? "{}"));
  if (url.endsWith("/databases/daily/query")) return Response.json({ results: [{ id: "daily-page" }], has_more: false });
  if (url.includes("/databases/") && url.endsWith("/query")) return Response.json({ results: saved ? [saved] : [], has_more: false });
  if (url.endsWith("/pages") && init?.method === "POST") {
    creates++; saved = { id: "session-page", properties: body.properties }; return Response.json(saved);
  }
  if (url.endsWith("/pages/daily-page")) {
    dailyUpdates++;
    assert.equal(body.properties["Study Minutes"].number, 30);
    assert.equal(body.properties["Study Sessions"].number, 1);
    return Response.json({ id: "daily-page" });
  }
  throw new Error("Unexpected Notion test request");
};

const env = { WORKERS_BEARER_TOKEN: "test-secret", NOTION_TOKEN: "test-notion", DAILY_LOG_DB_ID: "daily" } as any;
try {
  for (const duplicate of [false, true]) {
    const result = await worker.fetch(new Request("https://example.test/execute/api/study/session", {
      method: "POST", headers: { authorization: "Bearer test-secret", "content-type": "application/json" }, body: JSON.stringify(midnight),
    }), env);
    assert.equal(result.status, 200);
    const body = await result.json() as any;
    assert.equal(body.duplicate, duplicate);
    assert.equal(body.target_date, "2026-09-24");
    assert.equal(body.daily_log_updated, true);
  }
} finally { globalThis.fetch = originalFetch; }
assert.equal(creates, 1);
assert.equal(dailyUpdates, 2);
const response = await worker.fetch(new Request("https://example.test/study"), env);
assert.equal(response.status, 200);
assert.match(response.headers.get("content-type")!, /text\/html/);
assert.match(response.headers.get("content-security-policy")!, /frame-ancestors 'none'/);
const html = await response.text();
assert.ok(!html.includes("test-secret"));
assert.ok(!html.includes("test-notion"));
assert.match(html, /15,30,45,60,90,120/);
assert.equal((await worker.fetch(new Request("https://example.test/study", { method: "POST" }), env)).status, 405);
assert.equal((await worker.fetch(new Request("https://example.test/execute/api/study/session", {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(midnight),
}), env)).status, 401);
console.log("study entry date, validation, stable identity, routing and auth tests passed");
