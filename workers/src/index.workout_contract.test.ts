import assert from "node:assert/strict";
import worker from "./index";

const originalFetch = globalThis.fetch;
const target = "2026-11-05";
const zero = { ok: true, target_date: target, workout_done: false, sessions: 0, duration_min: 0, sets: 0, volume_kg: 0, calories: 0 };
let writes: Record<string, any>[] = [];
globalThis.fetch = async (url, init) => {
  if (String(url).endsWith("/query")) return Response.json({ results: [], has_more: false });
  if (String(url).endsWith("/pages") && init?.method === "POST") {
    writes.push(JSON.parse(String(init.body)));
    return Response.json({ id: "new-daily" });
  }
  return Response.json({ properties: {
    "Workout Done": { type: "checkbox" }, "Workout Sessions": { type: "number" },
    "Workout Duration Min": { type: "number" }, "Workout Sets": { type: "number" },
    "Workout Volume Kg": { type: "number" }, "Workout Calories": { type: "number" },
    "Data Status JSON": { type: "rich_text" },
  } });
};
try {
  for (const payload of [{}, { ...zero, target_date: "2026-11-04" }, { ...zero, sessions: "0" }, zero]) {
    writes = [];
    const response = await worker.fetch(new Request("https://worker.test/execute/api/daily_log/ingest_workout", {
      method: "POST", headers: { authorization: "Bearer worker", "content-type": "application/json" },
      body: JSON.stringify({ target_date: target }),
    }), { WORKERS_BEARER_TOKEN: "worker", NOTION_TOKEN: "notion", DAILY_LOG_DB_ID: "workout-test-db",
          WORKOUT_SERVICE: { fetch: async () => Response.json(payload) } } as any);
    if (payload === zero) {
      assert.equal(response.status, 200);
      assert.equal(writes.length, 1);
      assert.equal(writes[0].properties["Workout Sessions"].number, 0);
      const state = JSON.parse(writes[0].properties["Data Status JSON"].rich_text[0].text.content);
      assert.equal(state.workout.status, "confirmed_zero");
    } else {
      assert.equal(response.status, 502);
      assert.equal(writes.length, 0);
    }
  }
} finally {
  globalThis.fetch = originalFetch;
}
