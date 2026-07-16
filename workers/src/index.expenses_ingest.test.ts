import assert from "node:assert/strict";
import { __test__ } from "./index";

const { resolveExpensesAggregationWindow, parseExpenseTimestampMs } = __test__;

(() => {
  const window = resolveExpensesAggregationWindow("2026-05-15", 5);
  assert.equal(window.startJst, "2026-05-15T05:00:00+09:00");
  assert.equal(window.endJst, "2026-05-16T05:00:00+09:00");
})();

(() => {
  const page = {
    created_time: "2026-05-14T18:30:00.000Z",
    properties: {
      Date: { date: { start: "2026-05-15T09:00:00+09:00" } },
    },
  };
  const timestampMs = parseExpenseTimestampMs(page, "Date");
  assert.equal(new Date(timestampMs).toISOString(), "2026-05-15T00:00:00.000Z");
})();

(() => {
  const page = {
    created_time: "2026-05-15T02:00:00.000Z",
    properties: {
      Date: { date: null },
    },
  };
  const timestampMs = parseExpenseTimestampMs(page, "Date");
  assert.equal(new Date(timestampMs).toISOString(), "2026-05-15T02:00:00.000Z");
})();

console.log("index.expenses_ingest.test.ts: ok");
