export type StudySessionPayload = {
  session_id?: unknown;
  started_at?: unknown;
  ended_at?: unknown;
  duration_minutes?: unknown;
  target_date?: unknown;
  source?: unknown;
  subject?: unknown;
  study_type?: unknown;
  notes?: unknown;
};

export type ParsedStudySession = {
  sessionId: string;
  startedAt: string;
  endedAt: string;
  durationMinutes: number;
  targetDate: string;
  source: string;
  subject: string;
  studyType: string;
  notes: string;
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function requireString(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string") {
    throw new Error(`${field} must be a string`);
  }
  const text = value.trim();
  if (!text) {
    throw new Error(`${field} is required`);
  }
  if (text.length > maxLength) {
    throw new Error(`${field} must be ${maxLength} characters or fewer`);
  }
  return text;
}

function optionalString(value: unknown, field: string, maxLength: number): string {
  if (value === undefined || value === null || value === "") {
    return "";
  }
  if (typeof value !== "string") {
    throw new Error(`${field} must be a string`);
  }
  const text = value.trim();
  if (text.length > maxLength) {
    throw new Error(`${field} must be ${maxLength} characters or fewer`);
  }
  return text;
}

export function isValidDateString(value: string): boolean {
  if (!DATE_RE.test(value)) {
    return false;
  }
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function parseDayStartHour(raw: string | undefined): number {
  if (!raw) {
    return 4;
  }
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value < 0 || value > 23) {
    throw new Error("DAY_START_HOUR must be an integer from 0 to 23");
  }
  return value;
}

export function deriveStudyDate(endedAt: string, dayStartHour = 4): string {
  const ended = new Date(endedAt);
  if (Number.isNaN(ended.getTime())) {
    throw new Error("ended_at must be a valid ISO-8601 datetime");
  }
  const shifted = new Date(ended.getTime() + (9 - dayStartHour) * 60 * 60 * 1000);
  return shifted.toISOString().slice(0, 10);
}

export function parseStudySessionPayload(
  payload: StudySessionPayload,
  dayStartHour = 4,
): ParsedStudySession {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("request body must be a JSON object");
  }

  const sessionId = requireString(payload.session_id, "session_id", 120);
  const startedAt = requireString(payload.started_at, "started_at", 80);
  const endedAt = requireString(payload.ended_at, "ended_at", 80);
  const started = new Date(startedAt);
  const ended = new Date(endedAt);
  if (Number.isNaN(started.getTime())) {
    throw new Error("started_at must be a valid ISO-8601 datetime");
  }
  if (Number.isNaN(ended.getTime())) {
    throw new Error("ended_at must be a valid ISO-8601 datetime");
  }
  if (ended.getTime() <= started.getTime()) {
    throw new Error("ended_at must be later than started_at");
  }

  const durationMinutes = Math.max(1, Math.round((ended.getTime() - started.getTime()) / 60_000));
  if (durationMinutes > 24 * 60) {
    throw new Error("a study session cannot exceed 1440 minutes");
  }
  if (payload.duration_minutes !== undefined && payload.duration_minutes !== null) {
    if (typeof payload.duration_minutes !== "number" || !Number.isFinite(payload.duration_minutes)) {
      throw new Error("duration_minutes must be a finite number");
    }
    if (Math.abs(payload.duration_minutes - durationMinutes) > 2) {
      throw new Error("duration_minutes does not match started_at and ended_at");
    }
  }

  const derivedTargetDate = deriveStudyDate(endedAt, dayStartHour);
  const suppliedTargetDate = optionalString(payload.target_date, "target_date", 10);
  if (suppliedTargetDate) {
    if (!isValidDateString(suppliedTargetDate)) {
      throw new Error("target_date must use YYYY-MM-DD format");
    }
    if (suppliedTargetDate !== derivedTargetDate) {
      throw new Error(`target_date must be ${derivedTargetDate} for the configured day boundary`);
    }
  }

  return {
    sessionId,
    startedAt: started.toISOString(),
    endedAt: ended.toISOString(),
    durationMinutes,
    targetDate: derivedTargetDate,
    source: optionalString(payload.source, "source", 100) || "windows",
    subject: optionalString(payload.subject, "subject", 100),
    studyType: optionalString(payload.study_type, "study_type", 100),
    notes: optionalString(payload.notes, "notes", 1800),
  };
}
