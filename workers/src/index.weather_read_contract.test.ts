import assert from "node:assert/strict";
import worker, { __test__ } from "./index";

const rich = (plain_text: string) => ({ rich_text: [{ plain_text }] });
const retrievedAt = "2026-11-06T12:34:00+09:00";
const generatedAt = "2026-11-07T01:02:00+09:00";
const originalFetch = globalThis.fetch;
const scenarios = [
  { weather: { select: { name: "晴れ" } }, summary: rich("午後は曇り"), expectedWeather: "晴れ", expectedSummary: "午後は曇り" },
  { weather: { select: null }, summary: rich("晴れのち曇り"), expectedWeather: "晴れのち曇り", expectedSummary: "晴れのち曇り" },
  { weather: { select: { name: "曇り" } }, summary: rich(""), expectedWeather: "曇り", expectedSummary: "曇り" },
];

try {
  for (const [index, scenario] of scenarios.entries()) {
    let writes = 0;
    const env = { WORKERS_BEARER_TOKEN: "worker", NOTION_TOKEN: "notion", DAILY_LOG_DB_ID: `weather-contract-${index}` };
    const schema = Object.fromEntries(__test__.buildDailyLogProperties(env as any).map(({ name, type }) => [name, { type }]));
    const page = { id: "one-page", properties: {
      名前: { title: [{ plain_text: "Daily Log 2026-11-06" }] },
      Date: { date: { start: "2026-11-06" } }, "Target Date": { date: { start: "2026-11-06" } },
      Weather: scenario.weather, "Weather Summary": scenario.summary,
      // Exact properties must win over these otherwise matching aliases.
      weather: { select: { name: "誤った別項目" } }, weather_summary: rich("誤った別項目"),
      "Weather Retrieved At": { formula: { string: retrievedAt } },
      "Weather Generated At": rich(generatedAt),
    } };
    globalThis.fetch = async (url, init) => {
      const method = init?.method || "GET";
      if (method === "PATCH" || (method === "POST" && !String(url).endsWith("/query"))) writes++;
      assert.equal(writes, 0, "read route must not write");
      if (String(url).endsWith(`/databases/${env.DAILY_LOG_DB_ID}`)) return Response.json({ properties: schema });
      if (String(url).endsWith("/query")) return Response.json({ results: [page], has_more: false });
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    };
    const response = await worker.fetch(new Request("https://worker.test/api/daily_log?date=2026-11-06", {
      headers: { authorization: "Bearer worker" },
    }), env as any);
    assert.equal(response.status, 200);
    const data = await response.json() as any;
    assert.equal(data.weather, scenario.expectedWeather);
    assert.equal(data.weather_summary, scenario.expectedSummary);
    assert.equal(data.weather_retrieved_at, retrievedAt);
    assert.equal(data.weather_generated_at, generatedAt);
    assert.equal(writes, 0);
  }
} finally {
  globalThis.fetch = originalFetch;
}
console.log("Weather read HTTP contracts passed (3 cases)");
