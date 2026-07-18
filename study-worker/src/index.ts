import {
  parseDayStartHour,
  parseStudySessionPayload,
  type ParsedStudySession,
  type StudySessionPayload,
} from "./domain";

interface Env {
  NOTION_TOKEN: string;
  WORKERS_BEARER_TOKEN: string;
  STUDY_SESSIONS_DB_ID: string;
  DAILY_LOG_DB_ID: string;
  DAY_START_HOUR?: string;
  STUDY_SESSION_TITLE_PROPERTY?: string;
  STUDY_SESSION_ID_PROPERTY?: string;
  STUDY_SESSION_DATE_PROPERTY?: string;
  STUDY_SESSION_STARTED_AT_PROPERTY?: string;
  STUDY_SESSION_ENDED_AT_PROPERTY?: string;
  STUDY_SESSION_DURATION_PROPERTY?: string;
  STUDY_SESSION_SOURCE_PROPERTY?: string;
  STUDY_SESSION_SUBJECT_PROPERTY?: string;
  STUDY_SESSION_TYPE_PROPERTY?: string;
  STUDY_SESSION_NOTES_PROPERTY?: string;
  DAILY_LOG_TITLE_PROPERTY?: string;
  DAILY_LOG_DATE_PROPERTY?: string;
  DAILY_LOG_TARGET_DATE_PROPERTY?: string;
  DAILY_LOG_STUDY_MINUTES_PROPERTY?: string;
  DAILY_LOG_STUDY_SESSIONS_PROPERTY?: string;
  DAILY_LOG_STUDY_LAST_USED_AT_PROPERTY?: string;
  DAILY_LOG_SOURCE_PROPERTY?: string;
}

type NotionProperties = Record<string, { type?: string; [key: string]: unknown }>;

type SessionPropertyNames = {
  title: string;
  sessionId: string;
  studyDate: string;
  startedAt: string;
  endedAt: string;
  duration: string;
  source: string;
  subject: string;
  studyType: string;
  notes: string;
};

type DailyLogPropertyNames = {
  title: string;
  date: string;
  targetDate: string;
  studyMinutes: string;
  studySessions: string;
  studyLastUsedAt: string;
  source: string;
};

type StudyAggregate = {
  studyMinutes: number;
  studySessions: number;
  studyLastUsedAt: string | null;
};

const NOTION_VERSION = "2022-06-28";
const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, content-type",
  "access-control-allow-methods": "GET, POST, OPTIONS",
};

class HttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message);
  }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS });
}

function sessionPropertyNames(env: Env): SessionPropertyNames {
  return {
    title: env.STUDY_SESSION_TITLE_PROPERTY || "Name",
    sessionId: env.STUDY_SESSION_ID_PROPERTY || "Session ID",
    studyDate: env.STUDY_SESSION_DATE_PROPERTY || "Study Date",
    startedAt: env.STUDY_SESSION_STARTED_AT_PROPERTY || "Started At",
    endedAt: env.STUDY_SESSION_ENDED_AT_PROPERTY || "Ended At",
    duration: env.STUDY_SESSION_DURATION_PROPERTY || "Duration Minutes",
    source: env.STUDY_SESSION_SOURCE_PROPERTY || "Source",
    subject: env.STUDY_SESSION_SUBJECT_PROPERTY || "Subject",
    studyType: env.STUDY_SESSION_TYPE_PROPERTY || "Study Type",
    notes: env.STUDY_SESSION_NOTES_PROPERTY || "Notes",
  };
}

function dailyLogPropertyNames(env: Env): DailyLogPropertyNames {
  return {
    title: env.DAILY_LOG_TITLE_PROPERTY || "名前",
    date: env.DAILY_LOG_DATE_PROPERTY || "Date",
    targetDate: env.DAILY_LOG_TARGET_DATE_PROPERTY || "Target Date",
    studyMinutes: env.DAILY_LOG_STUDY_MINUTES_PROPERTY || "Study Minutes",
    studySessions: env.DAILY_LOG_STUDY_SESSIONS_PROPERTY || "Study Sessions",
    studyLastUsedAt: env.DAILY_LOG_STUDY_LAST_USED_AT_PROPERTY || "Study Last Used At",
    source: env.DAILY_LOG_SOURCE_PROPERTY || "Source",
  };
}

function requireEnv(env: Env): void {
  const missing = [
    "NOTION_TOKEN",
    "WORKERS_BEARER_TOKEN",
    "STUDY_SESSIONS_DB_ID",
    "DAILY_LOG_DB_ID",
  ].filter((name) => !(env as unknown as Record<string, string | undefined>)[name]?.trim());
  if (missing.length) {
    throw new HttpError(500, `missing Worker configuration: ${missing.join(", ")}`);
  }
}

function authenticate(request: Request, env: Env): void {
  const expected = env.WORKERS_BEARER_TOKEN?.trim();
  const actual = request.headers.get("authorization")?.trim() || "";
  if (!expected || actual !== `Bearer ${expected}`) {
    throw new HttpError(401, "unauthorized");
  }
}

async function notionFetch(env: Env, path: string, init: RequestInit = {}): Promise<any> {
  const response = await fetch(`https://api.notion.com/v1${path}`, {
    ...init,
    headers: {
      authorization: `Bearer ${env.NOTION_TOKEN}`,
      "notion-version": NOTION_VERSION,
      "content-type": "application/json",
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  let body: any = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { raw: text.slice(0, 500) };
    }
  }
  if (!response.ok) {
    throw new HttpError(502, `Notion API request failed (${response.status})`, {
      code: body?.code || null,
      message: body?.message || null,
      path,
    });
  }
  return body;
}

async function getDatabaseProperties(env: Env, databaseId: string): Promise<NotionProperties> {
  const database = await notionFetch(env, `/databases/${databaseId}`);
  return database?.properties || {};
}

function requireProperty(
  properties: NotionProperties,
  name: string,
  allowedTypes: string[],
  databaseLabel: string,
): string {
  const type = properties[name]?.type;
  if (!type || !allowedTypes.includes(type)) {
    throw new HttpError(
      500,
      `${databaseLabel} property ${name} must have type ${allowedTypes.join(" or ")}`,
      { actual_type: type || "missing" },
    );
  }
  return type;
}

function titleProperty(content: string): unknown {
  return { title: [{ type: "text", text: { content: content.slice(0, 2000) } }] };
}

function richTextProperty(content: string): unknown {
  return { rich_text: [{ type: "text", text: { content: content.slice(0, 2000) } }] };
}

function dateProperty(start: string): unknown {
  return { date: { start } };
}

function numberProperty(value: number): unknown {
  return { number: value };
}

function textLikeProperty(type: string, value: string): unknown {
  if (type === "select") {
    return { select: { name: value.slice(0, 100) } };
  }
  return richTextProperty(value);
}

function getNumber(property: any): number | null {
  return typeof property?.number === "number" && Number.isFinite(property.number)
    ? property.number
    : null;
}

function getDateStart(property: any): string | null {
  return typeof property?.date?.start === "string" ? property.date.start : null;
}

async function queryDatabaseAll(
  env: Env,
  databaseId: string,
  filter: Record<string, unknown>,
): Promise<any[]> {
  const pages: any[] = [];
  let startCursor: string | undefined;
  do {
    const body: Record<string, unknown> = { page_size: 100, filter };
    if (startCursor) {
      body.start_cursor = startCursor;
    }
    const response = await notionFetch(env, `/databases/${databaseId}/query`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    pages.push(...(response?.results || []));
    startCursor = response?.has_more && response?.next_cursor ? response.next_cursor : undefined;
  } while (startCursor);
  return pages;
}

async function findSessionById(
  env: Env,
  names: SessionPropertyNames,
  sessionId: string,
): Promise<any | null> {
  const pages = await queryDatabaseAll(env, env.STUDY_SESSIONS_DB_ID, {
    property: names.sessionId,
    rich_text: { equals: sessionId },
  });
  return pages[0] || null;
}

function buildSessionProperties(
  session: ParsedStudySession,
  names: SessionPropertyNames,
  schema: NotionProperties,
): Record<string, unknown> {
  requireProperty(schema, names.title, ["title"], "Study Sessions DB");
  requireProperty(schema, names.sessionId, ["rich_text"], "Study Sessions DB");
  requireProperty(schema, names.studyDate, ["date"], "Study Sessions DB");
  requireProperty(schema, names.startedAt, ["date"], "Study Sessions DB");
  requireProperty(schema, names.endedAt, ["date"], "Study Sessions DB");
  requireProperty(schema, names.duration, ["number"], "Study Sessions DB");
  const sourceType = requireProperty(schema, names.source, ["select", "rich_text"], "Study Sessions DB");

  const startedClock = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(session.startedAt));

  const properties: Record<string, unknown> = {
    [names.title]: titleProperty(`司法試験｜${session.targetDate} ${startedClock}｜${session.durationMinutes}分`),
    [names.sessionId]: richTextProperty(session.sessionId),
    [names.studyDate]: dateProperty(session.targetDate),
    [names.startedAt]: dateProperty(session.startedAt),
    [names.endedAt]: dateProperty(session.endedAt),
    [names.duration]: numberProperty(session.durationMinutes),
    [names.source]: textLikeProperty(sourceType, session.source),
  };

  if (session.subject && schema[names.subject]) {
    const type = requireProperty(schema, names.subject, ["select", "rich_text"], "Study Sessions DB");
    properties[names.subject] = textLikeProperty(type, session.subject);
  }
  if (session.studyType && schema[names.studyType]) {
    const type = requireProperty(schema, names.studyType, ["select", "rich_text"], "Study Sessions DB");
    properties[names.studyType] = textLikeProperty(type, session.studyType);
  }
  if (session.notes && schema[names.notes]) {
    requireProperty(schema, names.notes, ["rich_text"], "Study Sessions DB");
    properties[names.notes] = richTextProperty(session.notes);
  }
  return properties;
}

async function createSessionPage(
  env: Env,
  session: ParsedStudySession,
  names: SessionPropertyNames,
  schema: NotionProperties,
): Promise<any> {
  return notionFetch(env, "/pages", {
    method: "POST",
    body: JSON.stringify({
      parent: { database_id: env.STUDY_SESSIONS_DB_ID },
      properties: buildSessionProperties(session, names, schema),
    }),
  });
}

async function aggregateStudyDate(
  env: Env,
  names: SessionPropertyNames,
  targetDate: string,
): Promise<StudyAggregate> {
  const pages = await queryDatabaseAll(env, env.STUDY_SESSIONS_DB_ID, {
    property: names.studyDate,
    date: { equals: targetDate },
  });
  let studyMinutes = 0;
  let studyLastUsedAt: string | null = null;
  for (const page of pages) {
    const properties = page?.properties || {};
    studyMinutes += getNumber(properties[names.duration]) || 0;
    const endedAt = getDateStart(properties[names.endedAt]);
    if (endedAt && (!studyLastUsedAt || Date.parse(endedAt) > Date.parse(studyLastUsedAt))) {
      studyLastUsedAt = endedAt;
    }
  }
  return {
    studyMinutes,
    studySessions: pages.length,
    studyLastUsedAt,
  };
}

function buildDailyStudyProperties(
  aggregate: StudyAggregate,
  names: DailyLogPropertyNames,
  schema: NotionProperties,
): Record<string, unknown> {
  requireProperty(schema, names.studyMinutes, ["number"], "Daily Log DB");
  requireProperty(schema, names.studySessions, ["number"], "Daily Log DB");
  requireProperty(schema, names.studyLastUsedAt, ["date"], "Daily Log DB");
  const properties: Record<string, unknown> = {
    [names.studyMinutes]: numberProperty(aggregate.studyMinutes),
    [names.studySessions]: numberProperty(aggregate.studySessions),
  };
  if (aggregate.studyLastUsedAt) {
    properties[names.studyLastUsedAt] = dateProperty(aggregate.studyLastUsedAt);
  }
  return properties;
}

async function syncDailyLog(
  env: Env,
  targetDate: string,
  aggregate: StudyAggregate,
): Promise<{ page_id: string; created: boolean }> {
  const names = dailyLogPropertyNames(env);
  const schema = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  requireProperty(schema, names.targetDate, ["date"], "Daily Log DB");
  const pages = await queryDatabaseAll(env, env.DAILY_LOG_DB_ID, {
    property: names.targetDate,
    date: { equals: targetDate },
  });
  const studyProperties = buildDailyStudyProperties(aggregate, names, schema);

  if (pages[0]?.id) {
    await notionFetch(env, `/pages/${pages[0].id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: studyProperties }),
    });
    return { page_id: pages[0].id, created: false };
  }

  requireProperty(schema, names.title, ["title"], "Daily Log DB");
  const createProperties: Record<string, unknown> = {
    ...studyProperties,
    [names.title]: titleProperty(`Daily Log｜${targetDate}`),
    [names.targetDate]: dateProperty(targetDate),
  };
  if (schema[names.date]?.type === "date") {
    createProperties[names.date] = dateProperty(targetDate);
  }
  const sourceType = schema[names.source]?.type;
  if (sourceType === "select" || sourceType === "rich_text") {
    createProperties[names.source] = textLikeProperty(sourceType, "study-session-api");
  }

  const created = await notionFetch(env, "/pages", {
    method: "POST",
    body: JSON.stringify({
      parent: { database_id: env.DAILY_LOG_DB_ID },
      properties: createProperties,
    }),
  });
  return { page_id: created.id, created: true };
}

async function handleStudySession(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    throw new HttpError(405, "use POST /execute/api/study/session");
  }
  requireEnv(env);
  authenticate(request, env);

  let payload: StudySessionPayload;
  try {
    payload = (await request.json()) as StudySessionPayload;
  } catch {
    throw new HttpError(400, "invalid JSON body");
  }

  let session: ParsedStudySession;
  try {
    session = parseStudySessionPayload(payload, parseDayStartHour(env.DAY_START_HOUR));
  } catch (error) {
    throw new HttpError(400, error instanceof Error ? error.message : "invalid study session");
  }

  const names = sessionPropertyNames(env);
  const sessionSchema = await getDatabaseProperties(env, env.STUDY_SESSIONS_DB_ID);
  requireProperty(sessionSchema, names.sessionId, ["rich_text"], "Study Sessions DB");

  let page = await findSessionById(env, names, session.sessionId);
  const duplicate = Boolean(page?.id);
  if (!page) {
    page = await createSessionPage(env, session, names, sessionSchema);
  }

  const aggregate = await aggregateStudyDate(env, names, session.targetDate);
  let dailyLog: { page_id: string; created: boolean } | null = null;
  let dailyLogError: string | null = null;
  try {
    dailyLog = await syncDailyLog(env, session.targetDate, aggregate);
  } catch (error) {
    dailyLogError = error instanceof Error ? error.message : "Daily Log sync failed";
  }

  return jsonResponse(
    {
      ok: true,
      duplicate,
      session_page_id: page?.id || null,
      session_id: session.sessionId,
      target_date: session.targetDate,
      duration_minutes: session.durationMinutes,
      aggregate: {
        study_minutes: aggregate.studyMinutes,
        study_sessions: aggregate.studySessions,
        study_last_used_at: aggregate.studyLastUsedAt,
      },
      daily_log: dailyLog
        ? { synced: true, page_id: dailyLog.page_id, created: dailyLog.created }
        : { synced: false, error: dailyLogError },
    },
    duplicate ? 200 : 201,
  );
}

export async function handleRequest(request: Request, env: Env): Promise<Response> {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: JSON_HEADERS });
  }
  const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/";
  try {
    if (path === "/health" && request.method === "GET") {
      return jsonResponse({ ok: true, service: "study-session-api" });
    }
    if (path === "/execute/api/study/session") {
      return handleStudySession(request, env);
    }
    return jsonResponse({ error: "not_found" }, 404);
  } catch (error) {
    if (error instanceof HttpError) {
      return jsonResponse({ error: error.message, details: error.details || null }, error.status);
    }
    console.error("unhandled study worker error", error);
    return jsonResponse({ error: "internal_server_error" }, 500);
  }
}

export default {
  fetch: handleRequest,
};
