import assert from "node:assert/strict";

import {
  aggregateStudyPages,
  handleStudyAnkiDaily,
  handleStudyReconcile,
  parseAnkiDailyPayload,
  parseStudyDayStartHour,
  parseStudySessionPayload,
  resolveStudyTargetDate,
} from "./study_session";

assert.equal(parseStudyDayStartHour(undefined), 4);
assert.equal(parseStudyDayStartHour("0"), 0);
assert.throws(() => parseStudyDayStartHour("24"), /0 to 23/);

const session = parseStudySessionPayload({
  session_id: "windows-20260719-020000",
  started_at: "2026-07-19T02:00:00+09:00",
  ended_at: "2026-07-19T02:30:00+09:00",
});

assert.equal(session.durationMin, 30);
assert.equal(session.targetDate, "2026-07-18");
assert.equal(session.app, "Itojuku");
assert.equal(session.device, "Windows PC");
assert.equal(session.source, "shortcut");
assert.equal(
  resolveStudyTargetDate(Date.parse("2026-07-19T04:00:00+09:00"), 4),
  "2026-07-19",
);
assert.throws(
  () => parseStudySessionPayload({
    session_id: "bad",
    started_at: "2026-07-19T03:00:00+09:00",
    ended_at: "2026-07-19T02:00:00+09:00",
  }),
  /ended_at must be after started_at/,
);

const ankiDaily = parseAnkiDailyPayload({
  target_date: "2026-08-10",
  study_minutes: 90.17,
  study_sessions: 8,
  first_review_at: "2026-08-10T04:00:00+09:00",
  last_review_at: "2026-08-11T03:59:59+09:00",
  review_count: 137,
  max_time_review_count: 3,
  source: "anki_revlog",
});
assert.equal(ankiDaily.sessionId, "anki-revlog:2026-08-10");
assert.equal(ankiDaily.firstReviewAt, "2026-08-09T19:00:00.000Z");
assert.throws(
  () => parseAnkiDailyPayload({
    target_date: "2026-08-10",
    study_minutes: 1,
    study_sessions: 1,
    first_review_at: "2026-08-10T03:59:59+09:00",
    last_review_at: "2026-08-10T04:00:00+09:00",
    review_count: 1,
    max_time_review_count: 0,
  }),
  /04:00 JST study window/,
);
assert.deepEqual(parseAnkiDailyPayload({
  target_date: "2026-08-10",
  study_minutes: 0,
  study_sessions: 0,
  review_count: 0,
  max_time_review_count: 0,
}), {
  sessionId: "anki-revlog:2026-08-10",
  targetDate: "2026-08-10",
  studyMinutes: 0,
  studySessions: 0,
  firstReviewAt: null,
  lastReviewAt: null,
  reviewCount: 0,
  maxTimeReviewCount: 0,
  source: "anki_revlog",
});

function text(value: string) {
  return { rich_text: [{ plain_text: value }] };
}

function select(value: string) {
  return { select: { name: value } };
}

function page(input: {
  id: string;
  app: string;
  source: string;
  duration: number;
  end: string;
  sessionId?: string;
  sessionCount?: number;
  edited?: string;
}) {
  return {
    id: input.id,
    last_edited_time: input.edited ?? input.end,
    properties: {
      App: select(input.app),
      Source: select(input.source),
      "Session ID": text(input.sessionId ?? input.id),
      "Duration Min": { number: input.duration },
      "Session Count": { number: input.sessionCount ?? 0 },
      "End At": { date: { start: input.end } },
    },
  };
}

const legacyAnki = page({
  id: "legacy-anki",
  app: "Anki",
  source: "shortcut",
  duration: 20,
  end: "2026-08-10T08:00:00.000Z",
});
const pcAnki = page({
  id: "pc-anki",
  app: "Anki",
  source: "anki_revlog",
  sessionId: "anki-revlog:2026-08-10",
  duration: 90.17,
  sessionCount: 8,
  end: "2026-08-10T12:00:00.000Z",
});
const itojuku = page({
  id: "itojuku",
  app: "Itojuku",
  source: "shortcut",
  duration: 30,
  end: "2026-08-10T13:00:00.000Z",
});

assert.deepEqual(aggregateStudyPages([legacyAnki, pcAnki, itojuku]), {
  minutes: 120.17,
  sessions: 9,
  lastUsedAt: "2026-08-10T13:00:00.000Z",
  ankiRevlogAuthoritative: true,
});
assert.deepEqual(aggregateStudyPages([legacyAnki, itojuku]), {
  minutes: 50,
  sessions: 2,
  lastUsedAt: "2026-08-10T13:00:00.000Z",
  ankiRevlogAuthoritative: false,
});

const originalFetch = globalThis.fetch;
const unauthorizedResponse = await handleStudyAnkiDaily(new Request("https://worker.test/execute/api/study/anki-daily", {
  method: "POST",
  headers: { authorization: "Bearer wrong", "content-type": "application/json" },
  body: JSON.stringify({}),
}), { NOTION_TOKEN: "notion", DAILY_LOG_DB_ID: "daily", WORKERS_BEARER_TOKEN: "worker" });
assert.equal(unauthorizedResponse.status, 401);

let aggregateExists = false;
let createCount = 0;
let aggregateUpdateCount = 0;
let dailyUpdateCount = 0;
globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  if (url.endsWith("/databases/2ac8d44d07b4410b8f825906cab0c41e/query")) {
    if (body.filter?.property === "Session ID") {
      return Response.json({ results: aggregateExists ? [{ ...pcAnki, id: "pc-anki", url: "https://notion.test/pc-anki" }] : [], has_more: false });
    }
    return Response.json({ results: [legacyAnki, pcAnki, itojuku], has_more: false });
  }
  if (url.endsWith("/databases/daily/query")) {
    return Response.json({ results: [{ id: "daily-page", last_edited_time: "2026-08-11T00:00:00.000Z" }], has_more: false });
  }
  if (url.endsWith("/databases/missing-daily/query")) {
    return Response.json({ results: [], has_more: false });
  }
  if (url.endsWith("/pages") && init?.method === "POST") {
    aggregateExists = true;
    createCount += 1;
    return Response.json({ id: "pc-anki", url: "https://notion.test/pc-anki" });
  }
  if (url.endsWith("/pages/pc-anki")) {
    aggregateUpdateCount += 1;
    return Response.json({ id: "pc-anki", url: "https://notion.test/pc-anki" });
  }
  if (url.endsWith("/pages/daily-page")) {
    dailyUpdateCount += 1;
    return Response.json({ id: "daily-page" });
  }
  return Response.json({ error: "unexpected mock request", url, body }, { status: 500 });
};

const requestBody = {
  target_date: "2026-08-10",
  study_minutes: 90.17,
  study_sessions: 8,
  first_review_at: "2026-08-10T04:00:00+09:00",
  last_review_at: "2026-08-10T21:00:00+09:00",
  review_count: 137,
  max_time_review_count: 3,
};
const env = { NOTION_TOKEN: "notion", DAILY_LOG_DB_ID: "daily", WORKERS_BEARER_TOKEN: "worker" };
for (const expectedCreated of [true, false]) {
  const response = await handleStudyAnkiDaily(new Request("https://worker.test/execute/api/study/anki-daily", {
    method: "POST",
    headers: { authorization: "Bearer worker", "content-type": "application/json" },
    body: JSON.stringify(requestBody),
  }), env);
  assert.equal(response.status, 200);
  const result = await response.json() as any;
  assert.equal(result.created, expectedCreated);
  assert.equal(result.daily_totals.study_minutes, 120.17);
  assert.equal(result.daily_totals.study_sessions, 9);
  assert.equal(result.daily_totals.anki_revlog_authoritative, true);
}
const missingDailyResponse = await handleStudyReconcile(new Request("https://worker.test/execute/api/study/reconcile", {
  method: "POST",
  headers: { authorization: "Bearer worker", "content-type": "application/json" },
  body: JSON.stringify({ target_date: "2026-08-10" }),
}), { NOTION_TOKEN: "notion", DAILY_LOG_DB_ID: "missing-daily", WORKERS_BEARER_TOKEN: "worker" });
assert.equal(missingDailyResponse.status, 200);
const missingDailyResult = await missingDailyResponse.json() as any;
assert.equal(missingDailyResult.daily_log_updated, false);
globalThis.fetch = originalFetch;
assert.equal(createCount, 1);
assert.equal(aggregateUpdateCount, 1);
assert.equal(dailyUpdateCount, 2);

console.log("study_session.test.ts: ok");
