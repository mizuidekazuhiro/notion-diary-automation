import assert from "node:assert/strict";

import {
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

console.log("study_session.test.ts: ok");
