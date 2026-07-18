import assert from "node:assert/strict";

import { dispatchRoute, resolveRoutePath } from "./router";

const STUDY_SESSION_PATH = "/execute/api/study/session";
const DAILY_LOG_GENERATE_DIARY_PATH = "/execute/api/daily_log/generate_diary";

assert.equal(resolveRoutePath(STUDY_SESSION_PATH), DAILY_LOG_GENERATE_DIARY_PATH);
assert.equal(resolveRoutePath("/health"), "/health");

let called = 0;
const response = await dispatchRoute(STUDY_SESSION_PATH, {
  [DAILY_LOG_GENERATE_DIARY_PATH]: async () => {
    called += 1;
    return new Response(JSON.stringify({ ok: true }), {
      headers: { "content-type": "application/json" },
    });
  },
});

assert.equal(called, 1);
assert.ok(response);
assert.equal(response.status, 200);
assert.deepEqual(await response.json(), { ok: true });

const missing = await dispatchRoute("/missing", {});
assert.equal(missing, null);

console.log("router.test.ts: ok");
