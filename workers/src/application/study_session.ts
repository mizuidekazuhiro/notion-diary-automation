import {
  create_page,
  NotionApiError,
  queryDatabaseAll,
  update_page_property,
} from "../infrastructure/notion/client";

export const DEFAULT_APP_USAGE_SESSIONS_DB_ID = "2ac8d44d07b4410b8f825906cab0c41e";
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
): Promise<{ minutes: number; sessions: number; lastUsedAt: string | null }> {
  const pages = await queryDatabaseAll(env, databaseId, {
    property: "Target Date",
    date: { equals: targetDate },
  });
  let minutes = 0;
  let lastUsedAt: string | null = null;
  for (const page of pages) {
    minutes += getNumber(page, "Duration Min");
    const endedAt = getDate(page, "End At");
    if (endedAt && (!lastUsedAt || Date.parse(endedAt) > Date.parse(lastUsedAt))) {
      lastUsedAt = endedAt;
    }
  }
  return { minutes, sessions: pages.length, lastUsedAt };
}

async function syncDailyLogTotals(
  env: StudySessionEnv,
  targetDate: string,
  totals: { minutes: number; sessions: number; lastUsedAt: string | null },
): Promise<{ updated: boolean; pageId: string | null }> {
  const pages = await queryDatabaseAll(env, env.DAILY_LOG_DB_ID, {
    property: "Target Date",
    date: { equals: targetDate },
  });
  const page = pages
    .slice()
    .sort((a, b) => String(b.last_edited_time ?? "").localeCompare(String(a.last_edited_time ?? "")))[0];
  if (!page?.id) return { updated: false, pageId: null };

  const properties: Record<string, any> = {
    "Study Minutes": numberProperty(totals.minutes),
    "Study Sessions": numberProperty(totals.sessions),
  };
  if (totals.lastUsedAt) properties["Study Last Used At"] = dateProperty(totals.lastUsedAt);
  await update_page_property(env, page.id, properties);
  return { updated: true, pageId: page.id };
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

    const totals = await aggregateTargetDate(env, databaseId, session.targetDate);
    const dailyLog = await syncDailyLogTotals(env, session.targetDate, totals);
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
      },
      daily_log_updated: dailyLog.updated,
      daily_log_page_id: dailyLog.pageId,
    });
  } catch (error) {
    if (error instanceof NotionApiError) {
      return jsonResponse({
        error: "notion_error",
        status: error.status,
        code: error.code ?? null,
        message: error.notionMessage ?? error.message,
        request_id: error.requestId ?? null,
      }, error.status >= 400 ? error.status : 500);
    }
    console.error("study_session unexpected error", error);
    return jsonResponse({ error: "internal_error" }, 500);
  }
}
