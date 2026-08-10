import {
  create_page,
  NotionApiError,
  queryDatabaseAll,
  update_page_property,
} from "../infrastructure/notion/client";
import { chooseCanonicalDailyLogPage } from "../domain/daily_log_resolver";

export const DEFAULT_APP_USAGE_SESSIONS_DB_ID = "2ac8d44d07b4410b8f825906cab0c41e";
export const ANKI_REVLOG_SOURCE = "anki_revlog";
const DEFAULT_STUDY_DAY_START_HOUR = 4;
const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

type StudySessionEnv = {
  NOTION_TOKEN: string;
  DAILY_LOG_DB_ID: string;
  WORKERS_BEARER_TOKEN?: string;
  APP_USAGE_SESSIONS_DB_ID?: string;
  STUDY_DAY_START_HOUR?: string;
};

type StudySessionPayload = {
  session_id?: unknown;
  started_at?: unknown;
  ended_at?: unknown;
  app?: unknown;
  device?: unknown;
  source?: unknown;
};

type AnkiDailyPayload = {
  target_date?: unknown;
  study_minutes?: unknown;
  study_sessions?: unknown;
  first_review_at?: unknown;
  last_review_at?: unknown;
  review_count?: unknown;
  max_time_review_count?: unknown;
  source?: unknown;
};

type StudyReconcilePayload = {
  target_date?: unknown;
};

export type ParsedStudySession = {
  sessionId: string;
  startedAt: string;
  endedAt: string;
  durationMin: number;
  targetDate: string;
  app: string;
  device: string;
  source: string;
};

export type ParsedAnkiDaily = {
  sessionId: string;
  targetDate: string;
  studyMinutes: number;
  studySessions: number;
  firstReviewAt: string | null;
  lastReviewAt: string | null;
  reviewCount: number;
  maxTimeReviewCount: number;
  source: typeof ANKI_REVLOG_SOURCE;
};

export type StudyTotals = {
  minutes: number;
  sessions: number;
  lastUsedAt: string | null;
  ankiRevlogAuthoritative: boolean;
};

function jsonResponse(body: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function requiredString(value: unknown, field: string, maxLength = 200): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} is required`);
  }
  const result = value.trim();
  if (result.length > maxLength) {
    throw new Error(`${field} is too long`);
  }
  return result;
}

function optionalString(value: unknown, fallback: string, maxLength = 200): string {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value !== "string") throw new Error("optional text fields must be strings");
  const result = value.trim();
  if (!result) return fallback;
  if (result.length > maxLength) throw new Error("optional text field is too long");
  return result;
}

function requiredNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error(`${field} must be a non-negative number`);
  }
  return value;
}

function requiredInteger(value: unknown, field: string): number {
  const result = requiredNumber(value, field);
  if (!Number.isInteger(result)) throw new Error(`${field} must be an integer`);
  return result;
}

function parseTargetDate(value: unknown): string {
  const targetDate = requiredString(value, "target_date", 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate)) {
    throw new Error("target_date must be YYYY-MM-DD");
  }
  const start = Date.parse(`${targetDate}T04:00:00+09:00`);
  if (!Number.isFinite(start) || formatJstDate(start) !== targetDate) {
    throw new Error("target_date is not a valid calendar date");
  }
  return targetDate;
}

function optionalIsoDateTime(value: unknown, field: string): { iso: string; epochMs: number } | null {
  if (value === undefined || value === null || value === "") return null;
  return parseIsoDateTime(value, field);
}

function parseIsoDateTime(value: unknown, field: string): { iso: string; epochMs: number } {
  const raw = requiredString(value, field);
  const epochMs = Date.parse(raw);
  if (!Number.isFinite(epochMs)) throw new Error(`${field} must be an ISO-8601 datetime`);
  return { iso: new Date(epochMs).toISOString(), epochMs };
}

export function parseStudyDayStartHour(raw: string | undefined): number {
  if (raw === undefined || raw.trim() === "") return DEFAULT_STUDY_DAY_START_HOUR;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0 || value > 23) {
    throw new Error("STUDY_DAY_START_HOUR must be an integer from 0 to 23");
  }
  return value;
}

function formatJstDate(epochMs: number): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(epochMs));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function formatJstTime(epochMs: number): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(epochMs));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.hour}:${values.minute}`;
}

export function resolveStudyTargetDate(endedAtEpochMs: number, dayStartHour = 4): string {
  return formatJstDate(endedAtEpochMs - dayStartHour * 60 * 60 * 1000);
}

export function parseStudySessionPayload(
  payload: StudySessionPayload,
  dayStartHour = DEFAULT_STUDY_DAY_START_HOUR,
): ParsedStudySession {
  const sessionId = requiredString(payload.session_id, "session_id");
  const started = parseIsoDateTime(payload.started_at, "started_at");
  const ended = parseIsoDateTime(payload.ended_at, "ended_at");
  if (ended.epochMs <= started.epochMs) throw new Error("ended_at must be after started_at");

  const elapsedMinutes = (ended.epochMs - started.epochMs) / 60_000;
  if (elapsedMinutes > 24 * 60) throw new Error("study session cannot exceed 24 hours");

  return {
    sessionId,
    startedAt: started.iso,
    endedAt: ended.iso,
    durationMin: Math.max(1, Math.round(elapsedMinutes)),
    targetDate: resolveStudyTargetDate(ended.epochMs, dayStartHour),
    app: optionalString(payload.app, "Itojuku", 100),
    device: optionalString(payload.device, "Windows PC", 100),
    source: optionalString(payload.source, "shortcut", 100),
  };
}

export function parseAnkiDailyPayload(payload: AnkiDailyPayload): ParsedAnkiDaily {
  const targetDate = parseTargetDate(payload.target_date);
  const studyMinutes = requiredNumber(payload.study_minutes, "study_minutes");
  const studySessions = requiredInteger(payload.study_sessions, "study_sessions");
  const reviewCount = requiredInteger(payload.review_count, "review_count");
  const maxTimeReviewCount = requiredInteger(payload.max_time_review_count, "max_time_review_count");
  const firstReview = optionalIsoDateTime(payload.first_review_at, "first_review_at");
  const lastReview = optionalIsoDateTime(payload.last_review_at, "last_review_at");
  const source = optionalString(payload.source, ANKI_REVLOG_SOURCE, 100);

  if (source !== ANKI_REVLOG_SOURCE) throw new Error(`source must be ${ANKI_REVLOG_SOURCE}`);
  if (maxTimeReviewCount > reviewCount) {
    throw new Error("max_time_review_count cannot exceed review_count");
  }
  if (studySessions > reviewCount) {
    throw new Error("study_sessions cannot exceed review_count");
  }
  if (reviewCount === 0) {
    if (studyMinutes !== 0 || studySessions !== 0 || maxTimeReviewCount !== 0) {
      throw new Error("zero-review aggregates must have zero minutes, sessions, and max-time count");
    }
    if (firstReview || lastReview) {
      throw new Error("zero-review aggregates must not include review timestamps");
    }
  } else if (!firstReview || !lastReview) {
    throw new Error("first_review_at and last_review_at are required when review_count is positive");
  }

  if (firstReview && lastReview) {
    if (firstReview.epochMs > lastReview.epochMs) {
      throw new Error("last_review_at must not be before first_review_at");
    }
    const windowStart = Date.parse(`${targetDate}T04:00:00+09:00`);
    const windowEnd = windowStart + 24 * 60 * 60 * 1000;
    if (
      firstReview.epochMs < windowStart || firstReview.epochMs >= windowEnd
      || lastReview.epochMs < windowStart || lastReview.epochMs >= windowEnd
    ) {
      throw new Error("review timestamps must be inside the target date's 04:00 JST study window");
    }
  }

  return {
    sessionId: `anki-revlog:${targetDate}`,
    targetDate,
    studyMinutes,
    studySessions,
    firstReviewAt: firstReview?.iso ?? null,
    lastReviewAt: lastReview?.iso ?? null,
    reviewCount,
    maxTimeReviewCount,
    source: ANKI_REVLOG_SOURCE,
  };
}

function titleProperty(text: string) {
  return { title: [{ type: "text", text: { content: text } }] };
}

function richTextProperty(text: string) {
  return { rich_text: [{ type: "text", text: { content: text } }] };
}

function dateProperty(start: string) {
  return { date: { start } };
}

function selectProperty(name: string) {
  return { select: { name } };
}

function numberProperty(number: number) {
  return { number };
}

function getNumber(page: Record<string, any>, propertyName: string): number {
  const value = page.properties?.[propertyName]?.number;
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function getDate(page: Record<string, any>, propertyName: string): string | null {
  const value = page.properties?.[propertyName]?.date?.start;
  return typeof value === "string" && value ? value : null;
}

function getRichText(page: Record<string, any>, propertyName: string): string {
  const items = page.properties?.[propertyName]?.rich_text;
  if (!Array.isArray(items)) return "";
  return items.map((item: any) => item?.plain_text ?? item?.text?.content ?? "").join("");
}

function getSelect(page: Record<string, any>, propertyName: string): string {
  const value = page.properties?.[propertyName]?.select?.name;
  return typeof value === "string" ? value : "";
}

function isAnkiRevlogPage(page: Record<string, any>): boolean {
  return getSelect(page, "App") === "Anki"
    && (getSelect(page, "Source") === ANKI_REVLOG_SOURCE
      || getRichText(page, "Session ID").startsWith("anki-revlog:"));
}

function pageRecency(page: Record<string, any>): string {
  return String(page.last_edited_time ?? getDate(page, "End At") ?? "");
}

export function aggregateStudyPages(pages: Record<string, any>[]): StudyTotals {
  const revlogPages = pages.filter(isAnkiRevlogPage).sort((a, b) => pageRecency(b).localeCompare(pageRecency(a)));
  const authoritativeAnki = revlogPages[0] ?? null;
  const effectivePages = authoritativeAnki
    ? pages.filter((page) => getSelect(page, "App") !== "Anki" || page === authoritativeAnki)
    : pages;

  let minutes = 0;
  let sessions = 0;
  let lastUsedAt: string | null = null;
  for (const page of effectivePages) {
    minutes += getNumber(page, "Duration Min");
    sessions += page === authoritativeAnki ? getNumber(page, "Session Count") : 1;
    const endedAt = getDate(page, "End At");
    if (endedAt && (!lastUsedAt || Date.parse(endedAt) > Date.parse(lastUsedAt))) {
      lastUsedAt = endedAt;
    }
  }
  return {
    minutes: Math.round(minutes * 100) / 100,
    sessions,
    lastUsedAt,
    ankiRevlogAuthoritative: Boolean(authoritativeAnki),
  };
}

async function findSessionById(
  env: StudySessionEnv,
  databaseId: string,
  sessionId: string,
): Promise<Record<string, any> | null> {
  const pages = await queryDatabaseAll(env, databaseId, {
    property: "Session ID",
    rich_text: { equals: sessionId },
  });
  return pages[0] ?? null;
}

async function aggregateTargetDate(
  env: StudySessionEnv,
  databaseId: string,
  targetDate: string,
): Promise<StudyTotals> {
  const pages = await queryDatabaseAll(env, databaseId, {
    property: "Target Date",
    date: { equals: targetDate },
  });
  return aggregateStudyPages(pages);
}

async function syncDailyLogTotals(
  env: StudySessionEnv,
  targetDate: string,
  totals: StudyTotals,
): Promise<{ updated: boolean; pageId: string | null }> {
  const pages = await queryDatabaseAll(env, env.DAILY_LOG_DB_ID, {
    or: [
      { property: "Date", date: { equals: targetDate } },
      { property: "Target Date", date: { equals: targetDate } },
    ],
  });
  const page = chooseCanonicalDailyLogPage(pages as any[], targetDate);
  if (!page?.id) return { updated: false, pageId: null };

  const properties: Record<string, any> = {
    "Study Minutes": numberProperty(totals.minutes),
    "Study Sessions": numberProperty(totals.sessions),
    "Study Last Used At": totals.lastUsedAt ? dateProperty(totals.lastUsedAt) : { date: null },
  };
  await update_page_property(env, page.id, properties);
  return { updated: true, pageId: page.id };
}

function notionErrorResponse(error: NotionApiError): Response {
  return jsonResponse({
    error: "notion_error",
    status: error.status,
    code: error.code ?? null,
    message: error.notionMessage ?? error.message,
    request_id: error.requestId ?? null,
  }, error.status >= 400 ? error.status : 500);
}

async function reconcileTargetDate(
  env: StudySessionEnv,
  databaseId: string,
  targetDate: string,
): Promise<{ totals: StudyTotals; dailyLog: { updated: boolean; pageId: string | null } }> {
  const totals = await aggregateTargetDate(env, databaseId, targetDate);
  const dailyLog = await syncDailyLogTotals(env, targetDate, totals);
  return { totals, dailyLog };
}

export async function handleStudyAnkiDaily(request: Request, env: StudySessionEnv): Promise<Response> {
  if (request.method !== "POST") return jsonResponse({ error: "use POST /execute/api/study/anki-daily" }, 405);
  const authError = authorize(request, env);
  if (authError) return authError;

  let aggregate: ParsedAnkiDaily;
  try {
    aggregate = parseAnkiDailyPayload((await request.json()) as AnkiDailyPayload);
  } catch (error) {
    return jsonResponse({ error: error instanceof Error ? error.message : "invalid payload" }, 400);
  }

  const databaseId = env.APP_USAGE_SESSIONS_DB_ID?.trim() || DEFAULT_APP_USAGE_SESSIONS_DB_ID;
  const aggregateProperties: Record<string, any> = {
    Name: titleProperty(`Anki ${aggregate.targetDate}`),
    "Session ID": richTextProperty(aggregate.sessionId),
    "Duration Min": numberProperty(aggregate.studyMinutes),
    "Target Date": dateProperty(aggregate.targetDate),
    App: selectProperty("Anki"),
    Device: richTextProperty("Windows PC"),
    Source: selectProperty(aggregate.source),
    "Session Count": numberProperty(aggregate.studySessions),
    "Review Count": numberProperty(aggregate.reviewCount),
    "Max Time Review Count": numberProperty(aggregate.maxTimeReviewCount),
    "Start At": aggregate.firstReviewAt ? dateProperty(aggregate.firstReviewAt) : { date: null },
    "End At": aggregate.lastReviewAt ? dateProperty(aggregate.lastReviewAt) : { date: null },
  };

  try {
    const existing = await findSessionById(env, databaseId, aggregate.sessionId);
    let page: Record<string, any>;
    if (existing?.id) {
      page = await update_page_property(env, existing.id, aggregateProperties);
    } else {
      page = await create_page(env, databaseId, aggregateProperties);
    }
    const { totals, dailyLog } = await reconcileTargetDate(env, databaseId, aggregate.targetDate);
    return jsonResponse({
      ok: true,
      created: !existing,
      updated: Boolean(existing),
      session_id: aggregate.sessionId,
      session_page_id: page?.id ?? existing?.id ?? null,
      session_page_url: page?.url ?? existing?.url ?? null,
      target_date: aggregate.targetDate,
      anki: {
        study_minutes: aggregate.studyMinutes,
        study_sessions: aggregate.studySessions,
        first_review_at: aggregate.firstReviewAt,
        last_review_at: aggregate.lastReviewAt,
        review_count: aggregate.reviewCount,
        max_time_review_count: aggregate.maxTimeReviewCount,
      },
      daily_totals: {
        study_minutes: totals.minutes,
        study_sessions: totals.sessions,
        study_last_used_at: totals.lastUsedAt,
        anki_revlog_authoritative: totals.ankiRevlogAuthoritative,
      },
      daily_log_updated: dailyLog.updated,
      daily_log_page_id: dailyLog.pageId,
    });
  } catch (error) {
    if (error instanceof NotionApiError) return notionErrorResponse(error);
    console.error("study_anki_daily unexpected error", error);
    return jsonResponse({ error: "internal_error" }, 500);
  }
}

export async function handleStudyReconcile(request: Request, env: StudySessionEnv): Promise<Response> {
  if (request.method !== "POST") return jsonResponse({ error: "use POST /execute/api/study/reconcile" }, 405);
  const authError = authorize(request, env);
  if (authError) return authError;

  let targetDate: string;
  try {
    const payload = (await request.json()) as StudyReconcilePayload;
    targetDate = parseTargetDate(payload.target_date);
  } catch (error) {
    return jsonResponse({ error: error instanceof Error ? error.message : "invalid payload" }, 400);
  }

  const databaseId = env.APP_USAGE_SESSIONS_DB_ID?.trim() || DEFAULT_APP_USAGE_SESSIONS_DB_ID;
  try {
    const { totals, dailyLog } = await reconcileTargetDate(env, databaseId, targetDate);
    return jsonResponse({
      ok: true,
      target_date: targetDate,
      daily_totals: {
        study_minutes: totals.minutes,
        study_sessions: totals.sessions,
        study_last_used_at: totals.lastUsedAt,
        anki_revlog_authoritative: totals.ankiRevlogAuthoritative,
      },
      daily_log_updated: dailyLog.updated,
      daily_log_page_id: dailyLog.pageId,
    });
  } catch (error) {
    if (error instanceof NotionApiError) return notionErrorResponse(error);
    console.error("study_reconcile unexpected error", error);
    return jsonResponse({ error: "internal_error" }, 500);
  }
}

function authorize(request: Request, env: StudySessionEnv): Response | null {
  const expected = env.WORKERS_BEARER_TOKEN?.trim();
  if (!expected) return jsonResponse({ error: "WORKERS_BEARER_TOKEN is not configured" }, 500);
  const actual = request.headers.get("authorization") ?? "";
  if (actual !== `Bearer ${expected}`) return jsonResponse({ error: "unauthorized" }, 401);
  return null;
}

export async function handleStudySession(request: Request, env: StudySessionEnv): Promise<Response> {
  if (request.method !== "POST") return jsonResponse({ error: "use POST /execute/api/study/session" }, 405);
  const authError = authorize(request, env);
  if (authError) return authError;

  let rawPayload: StudySessionPayload;
  try {
    rawPayload = (await request.json()) as StudySessionPayload;
  } catch {
    return jsonResponse({ error: "invalid json body" }, 400);
  }

  let session: ParsedStudySession;
  try {
    session = parseStudySessionPayload(rawPayload, parseStudyDayStartHour(env.STUDY_DAY_START_HOUR));
  } catch (error) {
    return jsonResponse({ error: error instanceof Error ? error.message : "invalid payload" }, 400);
  }

  const databaseId = env.APP_USAGE_SESSIONS_DB_ID?.trim() || DEFAULT_APP_USAGE_SESSIONS_DB_ID;
  try {
    const existing = await findSessionById(env, databaseId, session.sessionId);
    let createdPage = existing;
    let duplicate = Boolean(existing);

    if (!existing) {
      const title = `${session.app} ${session.targetDate} ${formatJstTime(Date.parse(session.startedAt))}`;
      createdPage = await create_page(env, databaseId, {
        Name: titleProperty(title),
        "Session ID": richTextProperty(session.sessionId),
        "Start At": dateProperty(session.startedAt),
        "End At": dateProperty(session.endedAt),
        "Duration Min": numberProperty(session.durationMin),
        "Target Date": dateProperty(session.targetDate),
        App: selectProperty(session.app),
        Device: richTextProperty(session.device),
        Source: selectProperty(session.source),
      });
      duplicate = false;
    }

    const { totals, dailyLog } = await reconcileTargetDate(env, databaseId, session.targetDate);
    return jsonResponse({
      ok: true,
      created: !duplicate,
      duplicate,
      session_id: session.sessionId,
      session_page_id: createdPage?.id ?? null,
      session_page_url: createdPage?.url ?? null,
      target_date: session.targetDate,
      duration_min: session.durationMin,
      daily_totals: {
        study_minutes: totals.minutes,
        study_sessions: totals.sessions,
        study_last_used_at: totals.lastUsedAt,
        anki_revlog_authoritative: totals.ankiRevlogAuthoritative,
      },
      daily_log_updated: dailyLog.updated,
      daily_log_page_id: dailyLog.pageId,
    });
  } catch (error) {
    if (error instanceof NotionApiError) {
      return notionErrorResponse(error);
    }
    console.error("study_session unexpected error", error);
    return jsonResponse({ error: "internal_error" }, 500);
  }
}
