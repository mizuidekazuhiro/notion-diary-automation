import {
  addDaysToJstDate,
  getJstDateString,
  getJstYesterdayString,
  getJstDateStringFromDateTime,
  isValidDateString,
  formatJstDateTime,
} from "./utils/date_utils";
import { updateDailyLogTaskRelations } from "./application/daily_log_task_relations";
import {
  getNotionErrorDetails,
  NotionApiError,
  notionFetch,
  queryDatabaseAll,
} from "./infrastructure/notion/client";
import { formatMealSummary } from "./domain/meal_summary";
import {
  buildFallbackLocationSummary,
  resolveLocationWindow,
  segmentLocationLogs,
  type LocationSegment,
  type LocationSummaryResult,
  type NormalizedLocationLog,
} from "./domain/location_summary";
import {
  buildPhotoOnlyUpdateProperties,
  buildMealPhotoUpdateProperties,
  collectMealPhotosFromHealthPages,
  mergeNotionFilesDedup,
  resolveIngestTargetDate,
} from "./domain/daily_log_ingest";
import {
  getTaskPropertyNames,
  TaskPropertyNameEnv,
} from "./config/task_property_names";
import { TITLE_PROPERTIES } from "./config/title_properties";
import { dispatchRoute } from "./http/router";
import { buildDuplicateMergePatch, chooseCanonicalDailyLogPage, extractDailyLogDateFromTitle, isPageMatchedByDateOrTitle } from "./domain/daily_log_resolver";
import { ROUTES } from "./http/routes";

interface Env {
  NOTION_TOKEN: string;
  INBOX_DB_ID: string;
  TASK_DB_ID: string;
  DAILY_LOG_DB_ID: string;
  MAIL_LINK_SECRET?: string;
  EXPENSES_DB_ID?: string;
  HEALTH_DB_ID?: string;
  WORKERS_BEARER_TOKEN?: string;
  TASK_STATUS_DO?: string;
  TASK_STATUS_DONE?: string;
  TASK_STATUS_DROPPED?: string;
  TASK_STATUS_DROP_VALUE?: string;
  TASK_STATUS_SOMEDAY?: string;
  REQUIRE_STATUS_EXTRA_OPTIONS?: string;
  TASK_STATUS_PROPERTY_NAME?: string;
  TASK_DONE_DATE_PROPERTY_NAME?: string;
  TASK_DROP_DATE_PROPERTY_NAME?: string;
  HEALTH_DATE_PROPERTY_NAME?: string;
  HEALTH_PROTEIN_PROPERTY_NAME?: string;
  HEALTH_FAT_PROPERTY_NAME?: string;
  HEALTH_CARB_PROPERTY_NAME?: string;
  HEALTH_KCAL_PROPERTY_NAME?: string;
  HEALTH_WEIGHT_PROPERTY_NAME?: string;
  HEALTH_MEAL_PHOTO_PROPERTY_NAME?: string;
  HEALTH_SOURCE_PROPERTY_NAME?: string;
  HEALTH_SOURCE_VALUE?: string;
  DAILY_LOG_PROTEIN_PROPERTY_NAME?: string;
  DAILY_LOG_FAT_PROPERTY_NAME?: string;
  DAILY_LOG_CARB_PROPERTY_NAME?: string;
  DAILY_LOG_KCAL_PROPERTY_NAME?: string;
  DAILY_LOG_WEIGHT_PROPERTY_NAME?: string;
  DAILY_LOG_MEAL_PHOTO_PROPERTY_NAME?: string;
  DAILY_LOG_EXPENSES_TOTAL_PROPERTY_NAME?: string;
  DAILY_LOG_EXPENSES_RELATION_PROPERTY_NAME?: string;
  DAILY_LOG_NOTES_PROPERTY_NAME?: string;
  EXPENSES_DATE_PROPERTY_NAME?: string;
  EXPENSES_AMOUNT_PROPERTY_NAME?: string;
  EXPENSES_NAME_PROPERTY_NAME?: string;
  EXPENSES_MERCHANT_PROPERTY_NAME?: string;
  EXPENSES_DAY_START_HOUR?: string;
  LOCATION_LOG_DB_ID?: string;
  OPENAI_API_KEY?: string;
  TZ?: string;
  DAILY_LOG_DATE_PROP?: string;
  DAILY_LOG_LOCATION_SUMMARY_PROP?: string;
  LOCATION_LOG_TIME_PROP?: string;
  LOCATION_LOG_PLACE_PROP?: string;
  LOCATION_LOG_LAT_PROP?: string;
  LOCATION_LOG_LON_PROP?: string;
  LOCATION_LOG_SOURCE_PROP?: string;
  OPENAI_MODEL?: string;
  OPENAI_BASE_URL?: string;
  DRY_RUN?: string;
  LOCATION_ROUND_DECIMALS?: string;
  TIME_BUCKET_MINUTES?: string;
  WINDOW_START_HOUR?: string;
  DAILY_LOG_DIARY_NOTIFICATION_SENT_PROPERTY_NAME?: string;
  DAILY_LOG_DIARY_NOTIFICATION_HASH_PROPERTY_NAME?: string;
  DAILY_LOG_DIARY_NOTIFICATION_SENT_AT_PROPERTY_NAME?: string;
  DAILY_LOG_DIARY_NOTIFICATION_VERSION_PROPERTY_NAME?: string;
  DAILY_LOG_DIARY_GENERATED_AT_PROPERTY_NAME?: string;
  DAILY_LOG_DIARY_INPUT_HASH_PROPERTY_NAME?: string;
  DAILY_LOG_TODAY_ADVICE_INPUT_HASH_PROPERTY_NAME?: string;
  DAILY_LOG_TODAY_ADVICE_GENERATED_AT_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_SUMMARY_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_LOCATION_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_TEMP_MAX_C_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_TEMP_MIN_C_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_PRECIP_PROBABILITY_MAX_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_CODE_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_RETRIEVED_AT_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_INPUT_HASH_PROPERTY_NAME?: string;
  DAILY_LOG_WEATHER_GENERATED_AT_PROPERTY_NAME?: string;
}

type NotionPropertyType =
  | "title"
  | "rich_text"
  | "number"
  | "select"
  | "date"
  | "checkbox"
  | "relation"
  | "rollup"
  | "files";

type ExpectedProperty = {
  name: string;
  type: NotionPropertyType;
};

type SchemaCache = Record<string, boolean>;

const schemaCache: SchemaCache = {};
const databasePropertiesCache: Record<string, Record<string, any>> = {};

function normalizeNotionPropertyName(name: string): string {
  return name.trim().toLowerCase().replace(/[\s_-]+/g, "");
}

function findExactOrNormalizedPropertyMatches(
  properties: Record<string, any>,
  desiredName: string,
): string[] {
  if (!desiredName) {
    return [];
  }
  if (properties[desiredName]) {
    return [desiredName];
  }
  const normalizedDesired = normalizeNotionPropertyName(desiredName);
  return Object.keys(properties).filter((propertyName) => {
    return normalizeNotionPropertyName(propertyName) === normalizedDesired;
  });
}

function findPropertyMatchesWithAliases(
  properties: Record<string, any>,
  desiredName: string,
  aliases: string[] = [],
): string[] {
  const seen = new Set<string>();
  const matches: string[] = [];
  [desiredName, ...aliases].forEach((name) => {
    for (const match of findExactOrNormalizedPropertyMatches(properties, name)) {
      if (seen.has(match)) {
        continue;
      }
      seen.add(match);
      matches.push(match);
    }
  });
  return matches;
}

function resolvePropertyName(
  properties: Record<string, any>,
  desiredName: string,
  context: string,
  aliases: string[] = [],
): string | null {
  const matches = findPropertyMatchesWithAliases(properties, desiredName, aliases);
  if (!matches.length) {
    return null;
  }
  if (matches.length > 1) {
    console.warn(
      `[property_resolver] ambiguous match context=${context} desired=${desiredName} matches=${matches.join(", ")}`,
    );
    return null;
  }
  return matches[0];
}

function resolveExactPropertyName(
  properties: Record<string, any>,
  desiredName: string,
  context: string,
): string | null {
  const matches = findExactOrNormalizedPropertyMatches(properties, desiredName);
  if (!matches.length) {
    return null;
  }
  if (matches.length > 1) {
    console.warn(
      `[property_resolver] ambiguous match context=${context} desired=${desiredName} matches=${matches.join(", ")}`,
    );
    return null;
  }
  return matches[0];
}

function buildDailyLogProperties(env: Env): ExpectedProperty[] {
  const dailyLogExpenses = getDailyLogExpensesPropertyNames(env);
  return [
    { name: TITLE_PROPERTIES.dailyLog, type: "title" },
    { name: "Date", type: "date" },
    { name: "Target Date", type: "date" },
    { name: "Activity Summary", type: "rich_text" },
    { name: "Diary", type: "rich_text" },
    { name: "Today advice", type: "rich_text" },
    { name: DIARY_NOTIFICATION_SENT_PROPERTY_NAME, type: "checkbox" },
    { name: DIARY_NOTIFICATION_HASH_PROPERTY_NAME, type: "rich_text" },
    { name: DIARY_NOTIFICATION_SENT_AT_PROPERTY_NAME, type: "date" },
    { name: DIARY_NOTIFICATION_VERSION_PROPERTY_NAME, type: "number" },
    { name: DIARY_INPUT_HASH_PROPERTY_NAME, type: "rich_text" },
    { name: TODAY_ADVICE_INPUT_HASH_PROPERTY_NAME, type: "rich_text" },
    { name: dailyLogExpenses.total, type: "number" },
    { name: "Meal summary", type: "rich_text" },
    { name: "Weather Summary", type: "rich_text" },
    { name: "Weather Location", type: "rich_text" },
    { name: "Weather Temp Max C", type: "number" },
    { name: "Weather Temp Min C", type: "number" },
    { name: "Weather Precip Probability Max", type: "number" },
    { name: "Weather Code", type: "number" },
    { name: "Weather Retrieved At", type: "date" },
    { name: "Weather Input Hash", type: "rich_text" },
    { name: "Weather Generated At", type: "date" },
    { name: env.DAILY_LOG_LOCATION_SUMMARY_PROP || "Location summary (GPT)", type: "rich_text" },
    { name: "Mail ID", type: "rich_text" },
    { name: MAIL_INPUT_HASH_PROPERTY_NAME, type: "rich_text" },
    { name: MAIL_INPUT_SNAPSHOT_PROPERTY_NAME, type: "rich_text" },
    { name: MAIL_SENT_AT_PROPERTY_NAME, type: "date" },
    { name: MAIL_VERSION_PROPERTY_NAME, type: "number" },
    { name: "Study Minutes", type: "number" },
    { name: "Study Sessions", type: "number" },
    { name: "Study Last Used At", type: "date" },
    { name: "Mood", type: "select" },
    { name: "Source", type: "select" },
    { name: "Weight", type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.sleepStart.displayName, type: "date" },
    { name: SLEEP_PROPERTY_MAPPINGS.sleepEnd.displayName, type: "date" },
    { name: SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.sleepScore.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.deepDurationMin.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.remDurationMin.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.readinessStars.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.readinessHrv.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.readinessBpm.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.baselineHrv.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.displayName, type: "number" },
    { name: SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp.displayName, type: "rich_text" },
    { name: SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp.displayName, type: "rich_text" },
    { name: DIARY_GENERATED_AT_PROPERTY_NAME, type: "date" },
    { name: TODAY_ADVICE_GENERATED_AT_PROPERTY_NAME, type: "date" },
  ];
}

const DAILY_LOG_RELATION_PROPERTIES: ExpectedProperty[] = [
  { name: "Date", type: "date" },
  { name: "Done Tasks", type: "relation" },
  { name: "Drop Tasks", type: "relation" },
];

const MOOD_OPTIONS = ["★", "★★", "★★★", "★★★★", "★★★★★"] as const;

const BODY_CHUNK_LENGTH = 1800;
const NOTES_RICH_TEXT_LIMIT = 2000;
const DIARY_RICH_TEXT_LIMIT = 4000;
const DIARY_NOTIFICATION_SENT_PROPERTY_NAME = "Diary Notification Sent";
const DIARY_NOTIFICATION_HASH_PROPERTY_NAME = "Diary Notification Hash";
const DIARY_NOTIFICATION_SENT_AT_PROPERTY_NAME = "Diary Notification Sent At";
const DIARY_NOTIFICATION_VERSION_PROPERTY_NAME = "Diary Notification Version";
const DIARY_GENERATED_AT_PROPERTY_NAME = "Diary Generated At";
const DIARY_INPUT_HASH_PROPERTY_NAME = "Diary Input Hash";
const TODAY_ADVICE_INPUT_HASH_PROPERTY_NAME = "Today Advice Input Hash";
const TODAY_ADVICE_GENERATED_AT_PROPERTY_NAME = "Today Advice Generated At";
const WEATHER_PROPERTY_NAME = "Weather";
const WEATHER_SUMMARY_PROPERTY_NAME = "Weather Summary";
const WEATHER_LOCATION_PROPERTY_NAME = "Weather Location";
const WEATHER_TEMP_MAX_C_PROPERTY_NAME = "Weather Temp Max C";
const WEATHER_TEMP_MIN_C_PROPERTY_NAME = "Weather Temp Min C";
const WEATHER_PRECIP_PROBABILITY_MAX_PROPERTY_NAME = "Weather Precip Probability Max";
const WEATHER_CODE_PROPERTY_NAME = "Weather Code";
const WEATHER_RETRIEVED_AT_PROPERTY_NAME = "Weather Retrieved At";
const WEATHER_INPUT_HASH_PROPERTY_NAME = "Weather Input Hash";
const WEATHER_GENERATED_AT_PROPERTY_NAME = "Weather Generated At";
const MAIL_INPUT_HASH_PROPERTY_NAME = "Mail Input Hash";
const MAIL_INPUT_SNAPSHOT_PROPERTY_NAME = "Mail Input Snapshot";
const MAIL_SENT_AT_PROPERTY_NAME = "Mail Sent At";
const MAIL_VERSION_PROPERTY_NAME = "Mail Version";
const WEATHER_SELECT_LABEL_BY_CODE: Record<number, string> = {
  0: "晴れ",
  1: "晴れ",
  2: "曇り",
  3: "曇り",
  45: "霧",
  48: "霧",
  51: "雨",
  53: "雨",
  55: "雨",
  61: "雨",
  63: "雨",
  65: "雨",
  71: "雪",
  73: "雪",
  75: "雪",
  80: "雨",
  81: "雨",
  82: "雨",
  95: "雷雨",
};
const WEATHER_SELECT_LABELS = ["晴れ", "曇り", "雨", "雪", "雷雨", "霧"] as const;

function buildTaskProperties(env: TaskPropertyNameEnv): ExpectedProperty[] {
  const { statusPropertyName, doneDatePropertyName, dropDatePropertyName } =
    getTaskPropertyNames(env);
  return [
    { name: statusPropertyName, type: "select" },
    { name: "Since Do", type: "date" },
    { name: "Priority", type: "select" },
    { name: TITLE_PROPERTIES.tasks, type: "title" },
    { name: doneDatePropertyName, type: "date" },
    { name: dropDatePropertyName, type: "date" },
    { name: "Event Date", type: "date" },
  ];
}

function buildDailyLogMoodNotesProperties(): ExpectedProperty[] {
  return [
    { name: TITLE_PROPERTIES.dailyLog, type: "title" },
    { name: "Date", type: "date" },
    { name: "Mood", type: "select" },
  ];
}

const INBOX_PROPERTIES: ExpectedProperty[] = [
  { name: TITLE_PROPERTIES.inbox, type: "title" },
];

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
};

const textHeaders = {
  "content-type": "text/plain; charset=utf-8",
};

function unauthorized(message = "unauthorized"): Response {
  return new Response(JSON.stringify({ error: "unauthorized", message }), {
    status: 401,
    headers: jsonHeaders,
  });
}

function badRequest(message: string): Response {
  return new Response(JSON.stringify({ error: message }), {
    status: 400,
    headers: jsonHeaders,
  });
}

function notFound(): Response {
  return new Response(JSON.stringify({ error: "not found" }), {
    status: 404,
    headers: jsonHeaders,
  });
}

function methodNotAllowed(message = "method not allowed"): Response {
  return new Response(JSON.stringify({ error: message }), {
    status: 405,
    headers: jsonHeaders,
  });
}

function healthCheck(): Response {
  return new Response(JSON.stringify({ status: "ok" }), {
    headers: jsonHeaders,
  });
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: jsonHeaders });
}

function createHtmlPage(title: string, body: string): Response {
  return new Response(
    `<!doctype html><html><head><meta charset="utf-8" /><title>${title}</title></head><body>${body}</body></html>`,
    {
      headers: { "content-type": "text/html; charset=utf-8" },
    },
  );
}

function createTextResponse(message: string, status = 200): Response {
  return new Response(message, { status, headers: textHeaders });
}

function normalizePath(path: string): string {
  if (path.length <= 1) {
    return path;
  }
  return path.replace(/\/+$/, "");
}

const textEncoder = new TextEncoder();
let mailLinkKeyPromise: Promise<CryptoKey> | null = null;

function base64UrlEncode(buffer: ArrayBuffer | Uint8Array): string {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlDecode(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const padding = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  const binary = atob(`${padded}${padding}`);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function getMailLinkKey(secret: string): Promise<CryptoKey> {
  if (!mailLinkKeyPromise) {
    mailLinkKeyPromise = crypto.subtle.importKey(
      "raw",
      textEncoder.encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign", "verify"],
    );
  }
  return mailLinkKeyPromise;
}

async function signMailPayload(payload: string, secret: string): Promise<string> {
  const key = await getMailLinkKey(secret);
  const signature = await crypto.subtle.sign("HMAC", key, textEncoder.encode(payload));
  return `${base64UrlEncode(textEncoder.encode(payload))}.${base64UrlEncode(signature)}`;
}

type MailLinkPayload = {
  date: string;
  exp: number;
};

class MailLinkTokenError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function parseMailLinkPayload(payload: string): MailLinkPayload {
  const params = new URLSearchParams(payload);
  const date = params.get("date")?.trim() ?? "";
  const expRaw = params.get("exp")?.trim() ?? "";
  if (!date || !expRaw) {
    throw new MailLinkTokenError("invalid token payload", 401);
  }
  if (!isValidDateString(date)) {
    throw new MailLinkTokenError("invalid date", 400);
  }
  const exp = Number(expRaw);
  if (!Number.isFinite(exp) || exp <= 0) {
    throw new MailLinkTokenError("invalid exp", 401);
  }
  return { date, exp };
}

async function verifyMailLinkToken(token: string, env: Env): Promise<MailLinkPayload> {
  if (!env.MAIL_LINK_SECRET) {
    throw new MailLinkTokenError("MAIL_LINK_SECRET is not set", 500);
  }
  const [payloadB64, signatureB64, extra] = token.split(".");
  if (!payloadB64 || !signatureB64 || extra) {
    throw new MailLinkTokenError("invalid token", 401);
  }
  let payloadBytes: Uint8Array;
  try {
    payloadBytes = base64UrlDecode(payloadB64);
  } catch {
    throw new MailLinkTokenError("invalid token payload", 401);
  }
  const payload = new TextDecoder().decode(payloadBytes);
  const key = await getMailLinkKey(env.MAIL_LINK_SECRET);
  const signature = await crypto.subtle.sign("HMAC", key, textEncoder.encode(payload));
  const expected = base64UrlEncode(signature);
  if (expected !== signatureB64) {
    throw new MailLinkTokenError("invalid token", 401);
  }
  const parsed = parseMailLinkPayload(payload);
  const now = Math.floor(Date.now() / 1000);
  if (now > parsed.exp) {
    throw new MailLinkTokenError("token expired", 403);
  }
  return parsed;
}

function normalizeMoodInput(rawMood: string): (typeof MOOD_OPTIONS)[number] | undefined {
  const trimmed = rawMood.trim();
  if (!trimmed) {
    return undefined;
  }
  if (MOOD_OPTIONS.includes(trimmed as (typeof MOOD_OPTIONS)[number])) {
    return trimmed as (typeof MOOD_OPTIONS)[number];
  }
  const number = Number(trimmed);
  if (Number.isInteger(number) && number >= 1 && number <= MOOD_OPTIONS.length) {
    return MOOD_OPTIONS[number - 1];
  }
  return undefined;
}

function getExpensesDayStartHour(env: Env): number {
  const raw = env.EXPENSES_DAY_START_HOUR ?? "5";
  const dayStartHour = Number(raw);
  if (!Number.isInteger(dayStartHour) || dayStartHour < 0 || dayStartHour > 23) {
    throw new Error(
      `EXPENSES_DAY_START_HOUR must be an integer between 0 and 23. raw=${raw}`,
    );
  }
  console.log(
    `INFO: EXPENSES_DAY_START_HOUR raw=${JSON.stringify(raw)} parsed=${dayStartHour}`,
  );
  return dayStartHour;
}

function resolveExpensesAggregationWindow(targetDate: string, dayStartHour: number): {
  startJst: string;
  endJst: string;
} {
  const startJst = formatJstDateTime(targetDate, formatJstTimeFromHour(dayStartHour));
  const endJst = formatJstDateTime(
    addDaysToJstDate(targetDate, 1),
    formatJstTimeFromHour(dayStartHour),
  );
  return { startJst, endJst };
}

function formatJstTimeFromHour(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00:00`;
}

function parseExpenseCreatedTimeMs(page: Record<string, any>): number {
  const raw = typeof page.created_time === "string" ? page.created_time : "";
  const timestampMs = Date.parse(raw);
  return Number.isNaN(timestampMs) ? Number.NaN : timestampMs;
}

function parseExpenseDatePropertyMs(
  page: Record<string, any>,
  expensesDatePropertyName: string,
): number {
  const raw = page.properties?.[expensesDatePropertyName]?.date?.start;
  if (typeof raw !== "string" || !raw) {
    return Number.NaN;
  }
  const timestampMs = Date.parse(raw);
  return Number.isNaN(timestampMs) ? Number.NaN : timestampMs;
}

function parseExpenseTimestampMs(
  page: Record<string, any>,
  expensesDatePropertyName: string,
): number {
  const datePropertyMs = parseExpenseDatePropertyMs(page, expensesDatePropertyName);
  if (Number.isFinite(datePropertyMs)) {
    return datePropertyMs;
  }
  return parseExpenseCreatedTimeMs(page);
}

function isFamilyCardExpense(page: Record<string, any>): boolean {
  const familyCardProperty = page.properties?.FamilyCard;
  return (
    familyCardProperty?.type === "checkbox"
    && familyCardProperty.checkbox === true
  );
}

function formatDebugJstDateTime(timestampMs: number): string {
  if (!Number.isFinite(timestampMs)) {
    return "invalid";
  }
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(timestampMs));
}

async function notionErrorResponse(
  response: Response,
  context: string,
): Promise<Response> {
  const details = await getNotionErrorDetails(response);
  const bodySnippet =
    details.body.length > 4000
      ? `${details.body.slice(0, 4000)}...(truncated)`
      : details.body;
  const requestIdLog = details.requestId ? ` request_id=${details.requestId}` : "";
  console.error(
    `Notion API error in ${context}: status=${details.status}${requestIdLog} ${details.message}`,
  );
  console.error(`Notion API response body: ${bodySnippet}`);
  const status = details.status >= 400 ? details.status : 500;
  return new Response(
    JSON.stringify({
      error: "notion_error",
      status,
      code: details.code ?? null,
      message: details.notionMessage ?? null,
      request_id: details.requestId ?? null,
      body: details.body,
    }),
    {
      status,
      headers: jsonHeaders,
    },
  );
}

function notionErrorResponseFromDetails(details: {
  status: number;
  code?: string;
  notionMessage?: string;
  requestId?: string;
  body: string;
}): Response {
  const status = details.status >= 400 ? details.status : 500;
  return new Response(
    JSON.stringify({
      error: "notion_error",
      status,
      code: details.code ?? null,
      message: details.notionMessage ?? null,
      request_id: details.requestId ?? null,
      body: details.body,
    }),
    {
      status,
      headers: jsonHeaders,
    },
  );
}

async function parseJsonBody(request: Request): Promise<Record<string, any> | null> {
  try {
    const data = await request.json();
    if (data && typeof data === "object") {
      return data as Record<string, any>;
    }
    return null;
  } catch (error) {
    console.error("Failed to parse JSON body.", error);
    return null;
  }
}

function validateDailyLogPayload(payload: Record<string, any>): {
  data?: {
    targetDate: string;
    title: string;
    summaryText: string;
    summaryHtml: string;
    mailId: string;
    source: string;
    pageId?: string;
    updateTaskRelations: boolean;
    dataJson?: string;
  };
  error?: Response;
} {
  const targetDate =
    typeof payload.target_date === "string" ? payload.target_date.trim() : "";
  if (!targetDate) {
    return { error: badRequest("missing target_date") };
  }
  if (!isValidDateString(targetDate)) {
    return { error: badRequest("invalid target_date format") };
  }

  const title = typeof payload.title === "string" ? payload.title.trim() : "";
  if (!title) {
    return { error: badRequest("missing title") };
  }

  const summaryTextRaw =
    typeof payload.summary_text === "string"
      ? payload.summary_text
      : typeof payload.activity_summary === "string"
        ? payload.activity_summary
        : "";
  const summaryText = summaryTextRaw.trim();
  if (!summaryText) {
    return { error: badRequest("missing summary_text") };
  }

  const summaryHtml =
    typeof payload.summary_html === "string" ? payload.summary_html.trim() : "";

  const mailId =
    typeof payload.mail_id === "string" ? payload.mail_id.trim() : "";
  if (!mailId) {
    return { error: badRequest("missing mail_id") };
  }

  const source =
    typeof payload.source === "string" ? payload.source.trim() : "";
  if (!source) {
    return { error: badRequest("missing source") };
  }

  const pageId = typeof payload.page_id === "string" ? payload.page_id.trim() : "";
  const updateTaskRelations =
    payload.update_task_relations === undefined
      ? true
      : Boolean(payload.update_task_relations);

  const dataJson =
    typeof payload.data_json === "string" ? payload.data_json : undefined;
  if (payload.data_json !== undefined && typeof payload.data_json !== "string") {
    return { error: badRequest("data_json must be a string") };
  }

  return {
    data: {
      targetDate,
      title,
      summaryText,
      summaryHtml,
      mailId,
      source,
      ...(pageId ? { pageId } : {}),
      updateTaskRelations,
      dataJson,
    },
  };
}

function validateDailyLogEnsurePayload(payload: Record<string, any>): {
  data?: {
    targetDate: string;
    title: string;
    source: string;
    mailId: string;
  };
  error?: Response;
} {
  const targetDate =
    typeof payload.target_date === "string" ? payload.target_date.trim() : "";
  if (!targetDate) {
    return { error: badRequest("missing target_date") };
  }
  if (!isValidDateString(targetDate)) {
    return { error: badRequest("invalid target_date format") };
  }

  const title = typeof payload.title === "string" ? payload.title.trim() : "";
  if (!title) {
    return { error: badRequest("missing title") };
  }

  const source =
    typeof payload.source === "string" ? payload.source.trim() : "";
  if (!source) {
    return { error: badRequest("missing source") };
  }

  const mailId =
    typeof payload.mail_id === "string" ? payload.mail_id.trim() : "";
  if (!mailId) {
    return { error: badRequest("missing mail_id") };
  }

  return {
    data: {
      targetDate,
      title,
      source,
      mailId,
    },
  };
}

type MoodNotesMode = "append" | "replace";

function validateMoodNotesPayload(payload: Record<string, any>): {
  data?: {
    targetDate: string;
    mood?: (typeof MOOD_OPTIONS)[number];
    notes?: string;
    mode: MoodNotesMode;
    sourceUrl?: string;
  };
  error?: Response;
} {
  let targetDate = typeof payload.date === "string" ? payload.date.trim() : "";
  if (!targetDate) {
    targetDate = getJstDateString();
  }
  if (!isValidDateString(targetDate)) {
    return { error: badRequest("invalid date format") };
  }

  const rawMood = typeof payload.mood === "string" ? payload.mood.trim() : "";
  const mood = rawMood ? normalizeMoodInput(rawMood) : undefined;
  if (rawMood && !mood) {
    return { error: badRequest("invalid mood") };
  }

  let notes: string | undefined = undefined;
  if (payload.notes !== undefined) {
    if (typeof payload.notes !== "string") {
      return { error: badRequest("notes must be a string") };
    }
    notes = payload.notes;
  }

  const mode =
    typeof payload.mode === "string" && payload.mode.trim()
      ? payload.mode.trim().toLowerCase()
      : "append";
  if (mode !== "append" && mode !== "replace") {
    return { error: badRequest("invalid mode") };
  }

  let sourceUrl: string | undefined = undefined;
  if (payload.source_url !== undefined) {
    if (typeof payload.source_url !== "string") {
      return { error: badRequest("source_url must be a string") };
    }
    const trimmed = payload.source_url.trim();
    if (trimmed) {
      sourceUrl = trimmed;
    }
  }

  const notesValue = notes?.trim() ?? "";
  if (!mood && !notesValue && !sourceUrl) {
    return { error: badRequest("missing mood or notes") };
  }

  return {
    data: {
      targetDate,
      ...(mood ? { mood } : {}),
      ...(notes !== undefined ? { notes } : {}),
      mode: mode as MoodNotesMode,
      ...(sourceUrl ? { sourceUrl } : {}),
    },
  };
}

async function parseRequestBody(request: Request): Promise<Record<string, string>> {
  const contentType = request.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const payload = await parseJsonBody(request);
    return payload && typeof payload === "object" ? (payload as Record<string, string>) : {};
  }
  const formData = await request.formData();
  const entries: Record<string, string> = {};
  for (const [key, value] of formData as any as Iterable<[string, FormDataEntryValue]>) {
    if (typeof value === "string") {
      entries[key] = value;
    }
  }
  return entries;
}

function buildMoodNotesConfirmHtml(
  targetDate: string,
  token: string,
): Response {
  const moodButtons = [
    { value: "1", label: "最悪", emoji: "😣" },
    { value: "2", label: "悪い", emoji: "🙁" },
    { value: "3", label: "普通", emoji: "😐" },
    { value: "4", label: "良い", emoji: "🙂" },
    { value: "5", label: "最高", emoji: "😄" },
  ]
    .map(
      (option) => `
        <button
          type="button"
          data-mood="${option.value}"
          aria-pressed="false"
          style="
            min-width:56px;
            min-height:56px;
            padding:8px 10px;
            border-radius:12px;
            border:2px solid #e5e7eb;
            background:#f9fafb;
            color:#111827;
            cursor:pointer;
            font-size:14px;
            font-weight:600;
            display:flex;
            flex-direction:column;
            align-items:center;
            justify-content:center;
            gap:4px;
            flex:1 1 56px;
          "
        >
          <span style="font-size:20px;line-height:1;">${option.emoji}</span>
          <span>${option.value}</span>
          <span style="font-size:12px;font-weight:500;color:#374151;">${option.label}</span>
        </button>
      `,
    )
    .join("");

  const body = `
    <div style="max-width:640px;margin:0 auto;padding:24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
      <h1 style="font-size:20px;margin:0 0 8px;">Mood / Notes (${targetDate})</h1>
      <p style="margin:0 0 16px;color:#6b7280;">更新はPOSTのみで実行されます。</p>
      <form method="POST" action="/execute/mood-notes">
        <input type="hidden" name="date" value="${targetDate}" />
        <input type="hidden" name="token" value="${token}" />
        <input type="hidden" name="mode" value="append" />
        <input type="hidden" name="mood" id="mood-input" value="" />
        <div style="margin-bottom:8px;font-weight:600;">Mood</div>
        <div
          id="mood-buttons"
          style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;"
        >
          ${moodButtons}
        </div>
        <label for="notes" style="display:block;margin-bottom:8px;font-weight:600;">Notes</label>
        <textarea
          id="notes"
          name="notes"
          rows="6"
          placeholder="昨日の振り返りを日本語で…"
          style="width:100%;min-height:140px;resize:vertical;padding:12px;border-radius:12px;border:1px solid #d1d5db;font-size:15px;line-height:1.5;box-sizing:border-box;"
        ></textarea>
        <div style="margin-top:16px;">
          <button
            type="submit"
            style="width:100%;min-height:48px;border-radius:12px;border:1px solid #111827;background:#111827;color:#fff;font-size:16px;font-weight:600;cursor:pointer;"
          >
            保存
          </button>
        </div>
      </form>
    </div>
    <script>
      (function() {
        const moodInput = document.getElementById("mood-input");
        const buttons = Array.from(document.querySelectorAll("#mood-buttons [data-mood]"));
        const selectedStyle = {
          background: "#111827",
          color: "#f9fafb",
          borderColor: "#111827",
          boxShadow: "0 6px 12px rgba(17, 24, 39, 0.25)"
        };
        const resetStyle = {
          background: "#f9fafb",
          color: "#111827",
          borderColor: "#e5e7eb",
          boxShadow: "none"
        };

        buttons.forEach((button) => {
          button.addEventListener("click", () => {
            const value = button.getAttribute("data-mood") || "";
            moodInput.value = value;
            buttons.forEach((btn) => {
              btn.setAttribute("aria-pressed", "false");
              Object.assign(btn.style, resetStyle);
              const label = btn.querySelector("span:last-child");
              if (label) {
                label.style.color = "#374151";
              }
            });
            button.setAttribute("aria-pressed", "true");
            Object.assign(button.style, selectedStyle);
            const selectedLabel = button.querySelector("span:last-child");
            if (selectedLabel) {
              selectedLabel.style.color = "#f9fafb";
            }
          });
        });
      })();
    </script>
  `;
  return createHtmlPage("Mood / Notes", body);
}

async function updateMoodNotes(env: Env, data: {
  targetDate: string;
  mood?: (typeof MOOD_OPTIONS)[number];
  notes?: string;
  mode: MoodNotesMode;
  sourceUrl?: string;
}): Promise<Response> {
  const { targetDate, mood, notes, mode, sourceUrl } = data;

  const queryResponse = await notionFetch(
    env,
    `/databases/${env.DAILY_LOG_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 1,
        filter: {
          property: "Date",
          date: { equals: targetDate },
        },
      }),
    },
  );

  if (!queryResponse.ok) {
    return notionErrorResponse(queryResponse, "updateMoodNotes.query");
  }

  const queryData = await queryResponse.json();
  const existingPage = (queryData.results ?? [])[0] ?? null;
  const updateProperties: Record<string, any> = {};
  const notesPropertyName = getDailyLogNotesPropertyName(env);

  if (mood) {
    updateProperties.Mood = createSelectProperty(mood);
  }

  const shouldUpdateNotes = notes !== undefined || sourceUrl !== undefined;
  const notesValue = notes ?? "";
  const entry = shouldUpdateNotes ? buildMoodNotesEntry(notesValue, sourceUrl) : "";
  let notesUpdated = false;
  let updatedNotesText = "";
  let notesPropertyAvailable = true;

  if (shouldUpdateNotes) {
    const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
    if (!hasPropertyType(dailyLogProperties, notesPropertyName, "rich_text")) {
      console.warn(
        `Notes property "${notesPropertyName}" missing or not rich_text. Skipping notes update.`,
      );
      notesPropertyAvailable = false;
    }
  }

  if (shouldUpdateNotes && notesPropertyAvailable) {
    const existingText = existingPage
      ? getPlainTextFromRichText(existingPage.properties?.[notesPropertyName])
      : "";
    if (entry && shouldSkipAppend(existingText, entry)) {
      console.info("duplicate skip: mood-notes", {
        targetDate,
        mode,
        reason: "same_or_already_appended",
      });
    } else if (mode === "replace") {
      if (entry && normalizeNote(entry) !== normalizeNote(existingText)) {
        updatedNotesText = entry;
        notesUpdated = true;
      }
    } else if (entry) {
      updatedNotesText = existingText ? `${existingText}\n\n${entry}` : entry;
      notesUpdated = true;
    }
  }

  if (notesUpdated) {
    updateProperties[notesPropertyName] = createRichTextPropertyWithLimit(
      updatedNotesText,
      NOTES_RICH_TEXT_LIMIT,
    );
  }

  if (!Object.keys(updateProperties).length && !notesUpdated) {
    const diaryGenerated = false;
    const diaryGenerateReason = "skipped";
    return new Response(
      JSON.stringify({
        ok: true,
        updated: false,
        reason: "no changes",
        target_date: targetDate,
        found: Boolean(existingPage),
        page_id: existingPage?.id ?? null,
        diary_generated: diaryGenerated,
        diary_generate_reason: diaryGenerateReason,
      }),
      { headers: jsonHeaders },
    );
  }

  let pageId = existingPage?.id ?? null;

  if (Object.keys(updateProperties).length) {
    let resultResponse: Response;
    if (existingPage) {
      resultResponse = await notionFetch(env, `/pages/${existingPage.id}`, {
        method: "PATCH",
        body: JSON.stringify({ properties: updateProperties }),
      });
    } else {
      const properties: Record<string, any> = {
        [TITLE_PROPERTIES.dailyLog]: createTitleProperty(`Daily Log｜${targetDate}`),
        Date: createDateProperty(targetDate),
        ...updateProperties,
      };

      const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
      if (hasPropertyType(dailyLogProperties, "Target Date", "date")) {
        properties["Target Date"] = createDateProperty(targetDate);
      }

      resultResponse = await notionFetch(env, "/pages", {
        method: "POST",
        body: JSON.stringify({
          parent: { database_id: env.DAILY_LOG_DB_ID },
          properties,
        }),
      });
    }

    if (!resultResponse.ok) {
      return notionErrorResponse(resultResponse, "updateMoodNotes.upsert");
    }

    pageId = existingPage ? existingPage.id : (await resultResponse.json()).id;
  } else if (!pageId) {
    const properties: Record<string, any> = {
      [TITLE_PROPERTIES.dailyLog]: createTitleProperty(`Daily Log｜${targetDate}`),
      Date: createDateProperty(targetDate),
    };

    const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
    if (hasPropertyType(dailyLogProperties, "Target Date", "date")) {
      properties["Target Date"] = createDateProperty(targetDate);
    }

    const createResponse = await notionFetch(env, "/pages", {
      method: "POST",
      body: JSON.stringify({
        parent: { database_id: env.DAILY_LOG_DB_ID },
        properties,
      }),
    });

    if (!createResponse.ok) {
      return notionErrorResponse(createResponse, "updateMoodNotes.upsert");
    }

    pageId = (await createResponse.json()).id;
  }

  const diaryGenerated = false;
  const diaryGenerateReason = "skipped";

  return new Response(
    JSON.stringify({
      ok: true,
      updated: true,
      target_date: targetDate,
      page_id: pageId,
      diary_generated: diaryGenerated,
      diary_generate_reason: diaryGenerateReason,
    }),
    { headers: jsonHeaders },
  );
}


function buildDailyLogUpsertProperties(input: {
  title: string;
  targetDate: string;
  summaryText: string;
  mailId: string;
  source: string;
}): Record<string, any> {
  return {
    [TITLE_PROPERTIES.dailyLog]: createTitleProperty(input.title),
    "Target Date": createDateProperty(input.targetDate),
    Date: createDateProperty(input.targetDate),
    "Activity Summary": createRichTextProperty(input.summaryText),
    "Mail ID": createRichTextProperty(input.mailId),
    Source: createSelectProperty(input.source),
  };
}

function getMealPhotosFilesCount(properties: Record<string, any>): number {
  const files = properties["Meal Photos"]?.files;
  return Array.isArray(files) ? files.length : 0;
}

function buildDailyLogUpsertDiagnostics(input: {
  targetDate: string;
  pageId?: string;
  canonicalPageId: string | null;
  duplicateDetected: boolean;
  duplicateMergeCompleted: boolean;
  properties: Record<string, any>;
}): Record<string, any> {
  const patchPropertyKeys = Object.keys(input.properties);
  const resolvedUpdatePageId = input.pageId ?? input.canonicalPageId;
  return {
    target_date: input.targetDate,
    page_id: input.pageId ?? null,
    canonical_page_id: input.canonicalPageId,
    resolved_update_page_id: resolvedUpdatePageId,
    page_id_overrode_canonical: Boolean(input.pageId && input.canonicalPageId && input.pageId !== input.canonicalPageId),
    duplicate_detected: input.duplicateDetected,
    duplicate_merge_completed: input.duplicateMergeCompleted,
    patch_property_keys: patchPropertyKeys,
    patch_includes_meal_photos: patchPropertyKeys.includes("Meal Photos"),
    meal_photos_files_count: getMealPhotosFilesCount(input.properties),
  };
}

function sanitizeMealPhotosPatchProperties(properties: Record<string, any>): {
  sanitizedProperties: Record<string, any>;
  removedEmptyMealPhotos: boolean;
} {
  const sanitizedProperties = { ...properties };
  const mealPhotosProperty = sanitizedProperties["Meal Photos"];
  const files = Array.isArray(mealPhotosProperty?.files) ? mealPhotosProperty.files : null;
  const removedEmptyMealPhotos =
    Boolean(mealPhotosProperty) && Array.isArray(files) && files.length === 0;
  if (removedEmptyMealPhotos) {
    delete sanitizedProperties["Meal Photos"];
  }
  return { sanitizedProperties, removedEmptyMealPhotos };
}

function logDailyLogPatchKeys(input: {
  endpointName: string;
  targetDate: string;
  pageId: string | null;
  canonicalPageId: string | null;
  properties: Record<string, any>;
  reason: string;
}): void {
  const patchPropertyKeys = Object.keys(input.properties);
  console.log("DAILY_LOG_PATCH_KEYS", {
    endpoint_name: input.endpointName,
    target_date: input.targetDate,
    page_id: input.pageId,
    canonical_page_id: input.canonicalPageId,
    patch_property_keys: patchPropertyKeys,
    patch_includes_meal_photos: patchPropertyKeys.includes("Meal Photos"),
    meal_photos_files_count: getMealPhotosFilesCount(input.properties),
    reason: input.reason,
  });
}

function getSchemaCacheKey(
  dbId: string,
  expectedProperties: ExpectedProperty[],
  selectOptionRequirements: Record<string, string[]> = {},
): string {
  const propertiesKey = expectedProperties
    .map((property) => `${property.name}:${property.type}`)
    .join("|");
  const optionsKey = Object.entries(selectOptionRequirements)
    .map(([name, options]) => `${name}:${options.join(",")}`)
    .sort()
    .join("|");
  return `${dbId}:${propertiesKey}:${optionsKey}`;
}

async function validateDatabaseSchema(
  env: Env,
  dbId: string,
  expectedProperties: ExpectedProperty[],
  selectOptionRequirements: Record<string, string[]> = {},
): Promise<void> {
  const cacheKey = getSchemaCacheKey(dbId, expectedProperties, selectOptionRequirements);
  if (schemaCache[cacheKey]) {
    return;
  }

  const response = await notionFetch(env, `/databases/${dbId}`);
  if (!response.ok) {
    const details = await getNotionErrorDetails(response);
    throw new NotionApiError(details);
  }
  const data = await response.json();
  const properties = data.properties ?? {};

  const missing: string[] = [];
  const mismatched: string[] = [];
  const missingOptions: string[] = [];

  expectedProperties.forEach((property) => {
    const sleepMapping = Object.values(SLEEP_PROPERTY_MAPPINGS).find((item) => item.displayName === property.name);
    const resolvedName = resolvePropertyName(
      properties,
      property.name,
      `validate:${dbId}`,
      sleepMapping ? getSleepPropertyAliases(sleepMapping) : [],
    );
    const schema = resolvedName ? properties[resolvedName] : null;
    if (!schema) {
      missing.push(property.name);
      return;
    }
    if (schema.type !== property.type) {
      mismatched.push(`${property.name} (expected ${property.type}, got ${schema.type})`);
    }
  });

  Object.entries(selectOptionRequirements).forEach(([propertyName, requiredOptions]) => {
    const resolvedName = resolvePropertyName(properties, propertyName, `validate_select:${dbId}`);
    const schema = resolvedName ? properties[resolvedName] : null;
    if (!schema || schema.type !== "select") {
      return;
    }
    const options = schema.select?.options ?? [];
    const optionNames = new Set(options.map((option: { name: string }) => option.name));
    const missingForProperty = requiredOptions.filter((option) => !optionNames.has(option));
    if (missingForProperty.length) {
      missingOptions.push(`${propertyName} (${missingForProperty.join(", ")})`);
    }
  });

  if (missing.length) {
    console.warn(
      `Database schema warning: 存在しないプロパティ名かも -> ${missing.join(", ")}`,
    );
  }

  if (missing.length || mismatched.length || missingOptions.length) {
    const details = [
      missing.length ? `Missing: ${missing.join(", ")}` : null,
      mismatched.length ? `Mismatched: ${mismatched.join(", ")}` : null,
      missingOptions.length ? `Missing options: ${missingOptions.join(", ")}` : null,
    ]
      .filter(Boolean)
      .join("; ");
    throw new Error(`Database schema validation failed for ${dbId}: ${details}`);
  }

  schemaCache[cacheKey] = true;
}

async function getDatabaseProperties(
  env: Env,
  dbId: string,
): Promise<Record<string, any>> {
  if (databasePropertiesCache[dbId]) {
    return databasePropertiesCache[dbId];
  }
  const response = await notionFetch(env, `/databases/${dbId}`);
  if (!response.ok) {
    const details = await getNotionErrorDetails(response);
    throw new NotionApiError(details);
  }
  const data = await response.json();
  const properties = data.properties ?? {};
  databasePropertiesCache[dbId] = properties;
  return properties;
}

function hasPropertyType(
  properties: Record<string, any>,
  name: string,
  type: NotionPropertyType,
): boolean {
  const resolvedName = resolvePropertyName(properties, name, `hasPropertyType:${type}`);
  if (!resolvedName) {
    return false;
  }
  const schema = properties[resolvedName];
  return Boolean(schema && schema.type === type);
}

function getResolvedProperty<T>(
  properties: Record<string, T>,
  name: string,
  context: string,
  aliases: string[] = [],
): T | undefined {
  const resolvedName = resolvePropertyName(properties as Record<string, any>, name, context, aliases);
  return resolvedName ? properties[resolvedName] : undefined;
}

function resolvePropertyNameOrWarn(
  properties: Record<string, any>,
  desiredName: string,
  context: string,
  aliases: string[] = [],
): string | null {
  const resolvedName = resolvePropertyName(properties, desiredName, context, aliases);
  if (!resolvedName) {
    console.warn(`[property_resolver] missing match context=${context} desired=${desiredName}`);
    return null;
  }
  return resolvedName;
}

function canUseProperty(
  properties: Record<string, any>,
  desiredName: string,
  type: NotionPropertyType,
  context: string,
  aliases: string[] = [],
): string | null {
  const resolvedName = resolvePropertyNameOrWarn(properties, desiredName, context, aliases);
  if (!resolvedName) {
    return null;
  }
  const schema = properties[resolvedName];
  if (!schema || schema.type !== type) {
    console.warn(
      `[property_resolver] type mismatch context=${context} desired=${desiredName} resolved=${resolvedName} expected=${type} actual=${schema?.type ?? "missing"}`,
    );
    return null;
  }
  return resolvedName;
}
function getSleepPropertyAliases(mapping: SleepPropertyMapping): string[] {
  return mapping.aliases ?? [];
}

function parseBooleanEnv(value?: string): boolean {
  if (!value) {
    return false;
  }
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function getTaskStatusConfig(env: Env) {
  const doStatus = env.TASK_STATUS_DO || "Do";
  const doneStatus = env.TASK_STATUS_DONE || "Done";
  const droppedStatus =
    env.TASK_STATUS_DROPPED || env.TASK_STATUS_DROP_VALUE || "Drop";
  const somedayStatus = env.TASK_STATUS_SOMEDAY || "Someday";
  const requireExtraOptions = parseBooleanEnv(env.REQUIRE_STATUS_EXTRA_OPTIONS);
  return { doStatus, doneStatus, droppedStatus, somedayStatus, requireExtraOptions };
}

function getTaskStatusOptionRequirements(env: Env): Record<string, string[]> {
  const { doStatus, doneStatus, droppedStatus, requireExtraOptions } =
    getTaskStatusConfig(env);
  const { statusPropertyName } = getTaskPropertyNames(env);
  const extraOptions = requireExtraOptions ? ["Drop", "Someday"] : [];
  return {
    [statusPropertyName]: [doStatus, doneStatus, droppedStatus, ...extraOptions],
  };
}

type HealthPropertyNames = {
  date: string;
  protein: string;
  fat: string;
  carb: string;
  kcal: string;
  weight: string;
  mealPhoto: string;
  source: string;
  sleepStart: string;
  sleepEnd: string;
  sleepDurationMin: string;
  sleepScore: string;
  sleepSource: string;
  readinessStars: string;
  readinessHrv: string;
  readinessBpm: string;
  baselineHrv: string;
  baselineWakingBpm: string;
  sleepHeartRate: string;
  deepDurationMin: string;
  remDurationMin: string;
};

type DailyLogHealthPropertyNames = {
  protein: string;
  fat: string;
  carb: string;
  kcal: string;
  weight: string;
  mealPhoto: string;
  sleepStart: string;
  sleepEnd: string;
  sleepDurationMin: string;
  sleepScore: string;
  sleepSource: string;
  readinessStars: string;
  readinessHrv: string;
  readinessBpm: string;
  baselineHrv: string;
  baselineWakingBpm: string;
  sleepHeartRate: string;
  deepDurationMin: string;
  remDurationMin: string;
  sleepAnalysisJp: string;
  todayConditionForecastJp: string;
};

type SleepPropertyMapping = {
  displayName: string;
  internalName: string;
  aliases?: string[];
};

const SLEEP_PROPERTY_MAPPINGS: Record<string, SleepPropertyMapping> = {
  sleepStart: { displayName: "Sleep Start", internalName: "sleep_start" },
  sleepEnd: { displayName: "Sleep End", internalName: "sleep_end" },
  sleepDurationMin: { displayName: "Sleep Duration", internalName: "sleep_duration_min" },
  sleepScore: { displayName: "Sleep Score", internalName: "sleep_score" },
  sleepSource: { displayName: "Sleep Source", internalName: "sleep_source" },
  sleepHeartRate: { displayName: "Sleep Heart Rate", internalName: "sleep_heart_rate" },
  deepDurationMin: { displayName: "Deep Duration", internalName: "deep_duration_min" },
  remDurationMin: { displayName: "REM Duration", internalName: "rem_duration_min" },
  readinessStars: { displayName: "Readiness Stars", internalName: "readiness_stars" },
  readinessHrv: { displayName: "Readiness HRV", internalName: "readiness_hrv" },
  readinessBpm: { displayName: "Readiness BPM", internalName: "readiness_bpm" },
  baselineHrv: { displayName: "Baseline HRV", internalName: "baseline_hrv" },
  baselineWakingBpm: { displayName: "Baseline Waking BPM", internalName: "baseline_waking_bpm" },
  sleepAnalysisJp: {
    displayName: "Sleep Analysis JP",
    internalName: "sleep_analysis_jp",
    aliases: ["Sleep Analysis"],
  },
  todayConditionForecastJp: {
    displayName: "Today Condition Forecast JP",
    internalName: "today_condition_forecast_jp",
    aliases: ["Today Condition Forecast"],
  },
};

type ExpensesPropertyNames = {
  date: string;
  amount: string;
  name: string;
  merchant: string;
};

type DailyLogExpensesPropertyNames = {
  total: string;
  relation: string;
};

function getHealthPropertyNames(env: Env): HealthPropertyNames {
  return {
    date: env.HEALTH_DATE_PROPERTY_NAME || "Date",
    protein: env.HEALTH_PROTEIN_PROPERTY_NAME || "Protein",
    fat: env.HEALTH_FAT_PROPERTY_NAME || "Fat",
    carb: env.HEALTH_CARB_PROPERTY_NAME || "Carb",
    kcal: env.HEALTH_KCAL_PROPERTY_NAME || "Kcal",
    weight: env.HEALTH_WEIGHT_PROPERTY_NAME || "Weight",
    mealPhoto: env.HEALTH_MEAL_PHOTO_PROPERTY_NAME || "Meal Photos",
    source: env.HEALTH_SOURCE_PROPERTY_NAME || "Source",
    sleepStart: SLEEP_PROPERTY_MAPPINGS.sleepStart.internalName,
    sleepEnd: SLEEP_PROPERTY_MAPPINGS.sleepEnd.internalName,
    sleepDurationMin: SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.internalName,
    sleepScore: SLEEP_PROPERTY_MAPPINGS.sleepScore.internalName,
    sleepSource: SLEEP_PROPERTY_MAPPINGS.sleepSource.internalName,
    readinessStars: SLEEP_PROPERTY_MAPPINGS.readinessStars.internalName,
    readinessHrv: SLEEP_PROPERTY_MAPPINGS.readinessHrv.internalName,
    readinessBpm: SLEEP_PROPERTY_MAPPINGS.readinessBpm.internalName,
    baselineHrv: SLEEP_PROPERTY_MAPPINGS.baselineHrv.internalName,
    baselineWakingBpm: SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.internalName,
    sleepHeartRate: SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.internalName,
    deepDurationMin: SLEEP_PROPERTY_MAPPINGS.deepDurationMin.internalName,
    remDurationMin: SLEEP_PROPERTY_MAPPINGS.remDurationMin.internalName,
  };
}

function getDailyLogHealthPropertyNames(env: Env): DailyLogHealthPropertyNames {
  return {
    protein: env.DAILY_LOG_PROTEIN_PROPERTY_NAME || "Protein",
    fat: env.DAILY_LOG_FAT_PROPERTY_NAME || "Fat",
    carb: env.DAILY_LOG_CARB_PROPERTY_NAME || "Carb",
    kcal: env.DAILY_LOG_KCAL_PROPERTY_NAME || "Kcal",
    weight: env.DAILY_LOG_WEIGHT_PROPERTY_NAME || "Weight",
    mealPhoto: env.DAILY_LOG_MEAL_PHOTO_PROPERTY_NAME || "Meal Photos",
    sleepStart: SLEEP_PROPERTY_MAPPINGS.sleepStart.displayName,
    sleepEnd: SLEEP_PROPERTY_MAPPINGS.sleepEnd.displayName,
    sleepDurationMin: SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.displayName,
    sleepScore: SLEEP_PROPERTY_MAPPINGS.sleepScore.displayName,
    sleepSource: SLEEP_PROPERTY_MAPPINGS.sleepSource.displayName,
    readinessStars: SLEEP_PROPERTY_MAPPINGS.readinessStars.displayName,
    readinessHrv: SLEEP_PROPERTY_MAPPINGS.readinessHrv.displayName,
    readinessBpm: SLEEP_PROPERTY_MAPPINGS.readinessBpm.displayName,
    baselineHrv: SLEEP_PROPERTY_MAPPINGS.baselineHrv.displayName,
    baselineWakingBpm: SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.displayName,
    sleepHeartRate: SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.displayName,
    deepDurationMin: SLEEP_PROPERTY_MAPPINGS.deepDurationMin.displayName,
    remDurationMin: SLEEP_PROPERTY_MAPPINGS.remDurationMin.displayName,
    sleepAnalysisJp: SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp.displayName,
    todayConditionForecastJp: SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp.displayName,
  };
}

function getExpensesPropertyNames(env: Env): ExpensesPropertyNames {
  return {
    date: env.EXPENSES_DATE_PROPERTY_NAME || "Date",
    amount: env.EXPENSES_AMOUNT_PROPERTY_NAME || "Amount",
    name: env.EXPENSES_NAME_PROPERTY_NAME || "Name",
    merchant: env.EXPENSES_MERCHANT_PROPERTY_NAME || "Merchant",
  };
}

function getDailyLogExpensesPropertyNames(env: Env): DailyLogExpensesPropertyNames {
  return {
    total: env.DAILY_LOG_EXPENSES_TOTAL_PROPERTY_NAME || "Expenses total",
    relation: env.DAILY_LOG_EXPENSES_RELATION_PROPERTY_NAME || "Expenses",
  };
}

function getDailyLogNotesPropertyName(env: Env): string {
  return env.DAILY_LOG_NOTES_PROPERTY_NAME || "Notes";
}

function getDiaryNotificationSentPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_DIARY_NOTIFICATION_SENT_PROPERTY_NAME ||
    DIARY_NOTIFICATION_SENT_PROPERTY_NAME
  );
}

function getDiaryNotificationHashPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_DIARY_NOTIFICATION_HASH_PROPERTY_NAME ||
    DIARY_NOTIFICATION_HASH_PROPERTY_NAME
  );
}

function getDiaryNotificationSentAtPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_DIARY_NOTIFICATION_SENT_AT_PROPERTY_NAME ||
    DIARY_NOTIFICATION_SENT_AT_PROPERTY_NAME
  );
}

function getDiaryNotificationVersionPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_DIARY_NOTIFICATION_VERSION_PROPERTY_NAME ||
    DIARY_NOTIFICATION_VERSION_PROPERTY_NAME
  );
}

function getDiaryGeneratedAtPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_DIARY_GENERATED_AT_PROPERTY_NAME || DIARY_GENERATED_AT_PROPERTY_NAME
  );
}

function getDiaryInputHashPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_DIARY_INPUT_HASH_PROPERTY_NAME || DIARY_INPUT_HASH_PROPERTY_NAME
  );
}

function getTodayAdviceInputHashPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_TODAY_ADVICE_INPUT_HASH_PROPERTY_NAME ||
    TODAY_ADVICE_INPUT_HASH_PROPERTY_NAME
  );
}

function getTodayAdviceGeneratedAtPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_TODAY_ADVICE_GENERATED_AT_PROPERTY_NAME ||
    TODAY_ADVICE_GENERATED_AT_PROPERTY_NAME
  );
}

function getWeatherPropertyName(env: Env): string {
  return env.DAILY_LOG_WEATHER_PROPERTY_NAME || WEATHER_PROPERTY_NAME;
}

function getWeatherSummaryPropertyName(env: Env): string {
  return env.DAILY_LOG_WEATHER_SUMMARY_PROPERTY_NAME || WEATHER_SUMMARY_PROPERTY_NAME;
}

function getWeatherLocationPropertyName(env: Env): string {
  return env.DAILY_LOG_WEATHER_LOCATION_PROPERTY_NAME || WEATHER_LOCATION_PROPERTY_NAME;
}

function getWeatherTempMaxCPropertyName(env: Env): string {
  return env.DAILY_LOG_WEATHER_TEMP_MAX_C_PROPERTY_NAME || WEATHER_TEMP_MAX_C_PROPERTY_NAME;
}

function getWeatherTempMinCPropertyName(env: Env): string {
  return env.DAILY_LOG_WEATHER_TEMP_MIN_C_PROPERTY_NAME || WEATHER_TEMP_MIN_C_PROPERTY_NAME;
}

function getWeatherPrecipProbabilityMaxPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_WEATHER_PRECIP_PROBABILITY_MAX_PROPERTY_NAME ||
    WEATHER_PRECIP_PROBABILITY_MAX_PROPERTY_NAME
  );
}

function getWeatherCodePropertyName(env: Env): string {
  return env.DAILY_LOG_WEATHER_CODE_PROPERTY_NAME || WEATHER_CODE_PROPERTY_NAME;
}

function getWeatherRetrievedAtPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_WEATHER_RETRIEVED_AT_PROPERTY_NAME ||
    WEATHER_RETRIEVED_AT_PROPERTY_NAME
  );
}

function getWeatherInputHashPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_WEATHER_INPUT_HASH_PROPERTY_NAME || WEATHER_INPUT_HASH_PROPERTY_NAME
  );
}

function getWeatherGeneratedAtPropertyName(env: Env): string {
  return (
    env.DAILY_LOG_WEATHER_GENERATED_AT_PROPERTY_NAME || WEATHER_GENERATED_AT_PROPERTY_NAME
  );
}

type LocationPropertyNames = {
  time: string;
  place: string;
  lat: string;
  lon: string;
  source: string;
};

function getDailyLogDatePropertyName(env: Env): string {
  return env.DAILY_LOG_DATE_PROP || "Date";
}

function getLocationPropertyNames(env: Env): LocationPropertyNames {
  return {
    time: env.LOCATION_LOG_TIME_PROP || "Time",
    place: env.LOCATION_LOG_PLACE_PROP || "Place",
    lat: env.LOCATION_LOG_LAT_PROP || "Latitude (raw)",
    lon: env.LOCATION_LOG_LON_PROP || "Longitude (raw)",
    source: env.LOCATION_LOG_SOURCE_PROP || "Source",
  };
}

function parseBooleanEnvWithDefault(value: string | undefined, defaultValue: boolean): boolean {
  if (typeof value !== "string") {
    return defaultValue;
  }
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function parseIntEnv(value: string | undefined, defaultValue: number): number {
  if (!value) {
    return defaultValue;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

async function validateTasksDatabaseSchema(env: Env): Promise<void> {
  await validateDatabaseSchema(
    env,
    env.TASK_DB_ID,
    buildTaskProperties(env),
    getTaskStatusOptionRequirements(env),
  );
}

function getPageTitleFromProperty(
  page: Record<string, any>,
  propertyName: string,
): string {
  const titleProp = page.properties?.[propertyName]?.title;
  if (!Array.isArray(titleProp)) {
    return "";
  }
  return titleProp.map((item: { plain_text: string }) => item.plain_text).join("");
}

function createTitleProperty(title: string) {
  return {
    title: [
      {
        text: { content: title },
      },
    ],
  };
}

function createRichTextProperty(content: string) {
  const chunks = splitIntoChunks(content, BODY_CHUNK_LENGTH);
  return {
    rich_text: chunks.map((chunk) => ({
      text: { content: chunk },
    })),
  };
}

function createRichTextPropertyWithLimit(content: string, limit = NOTES_RICH_TEXT_LIMIT) {
  const items = splitRichText(content, limit);
  return {
    rich_text: items,
  };
}

function createDateProperty(date: string) {
  return {
    date: date ? { start: date } : null,
  };
}

function createSelectProperty(name: string) {
  return {
    select: name ? { name } : null,
  };
}

function createNumberProperty(value?: number | null) {
  return {
    number: typeof value === "number" ? value : null,
  };
}

function createFilesProperty(files: Array<Record<string, any>>) {
  return {
    files,
  };
}

function createRelationProperty(ids: string[]) {
  return {
    relation: ids.map((id) => ({ id })),
  };
}

function createCheckboxProperty(value: boolean) {
  return {
    checkbox: value,
  };
}

function hasNonEmptyValue(value: unknown): boolean {
  if (value === null || value === undefined) {
    return false;
  }
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  return true;
}

function buildWeatherSummaryText(params: {
  weatherCode: number | null;
  weatherTempMaxC: number | null;
  weatherTempMinC: number | null;
  weatherPrecipProbabilityMax: number | null;
}): string {
  const weatherLabel =
    params.weatherCode !== null ? WEATHER_CODE_LABELS[params.weatherCode] ?? "" : "";
  const metricParts: string[] = [];
  if (params.weatherTempMaxC !== null) {
    metricParts.push(`最高${params.weatherTempMaxC.toFixed(1)}℃`);
  }
  if (params.weatherTempMinC !== null) {
    metricParts.push(`最低${params.weatherTempMinC.toFixed(1)}℃`);
  }
  if (!weatherLabel && !metricParts.length && params.weatherPrecipProbabilityMax === null) {
    return "";
  }
  const firstSentence = weatherLabel ? `${weatherLabel}。` : "";
  let secondSentence = "";
  if (metricParts.length && params.weatherPrecipProbabilityMax !== null) {
    secondSentence = `${metricParts.join("、")}、降水確率は${params.weatherPrecipProbabilityMax}%です。`;
  } else if (metricParts.length) {
    secondSentence = `${metricParts.join("、")}です。`;
  } else if (params.weatherPrecipProbabilityMax !== null) {
    secondSentence = `降水確率は${params.weatherPrecipProbabilityMax}%です。`;
  }
  return `${firstSentence}${secondSentence}`;
}

const WEATHER_CODE_LABELS: Record<number, string> = {
  0: "晴れ",
  1: "概ね晴れ",
  2: "一部くもり",
  3: "くもり",
  45: "霧",
  48: "着氷性の霧",
  51: "弱い霧雨",
  53: "霧雨",
  55: "強い霧雨",
  61: "弱い雨",
  63: "雨",
  65: "強い雨",
  71: "弱い雪",
  73: "雪",
  75: "強い雪",
  80: "弱いにわか雨",
  81: "にわか雨",
  82: "強いにわか雨",
  95: "雷雨",
};

function inferWeatherSelectLabel(weatherCode: number | null, summaryText: string): string | null {
  if (weatherCode !== null && WEATHER_SELECT_LABEL_BY_CODE[weatherCode]) {
    return WEATHER_SELECT_LABEL_BY_CODE[weatherCode];
  }
  const normalized = summaryText.trim();
  if (!normalized) {
    return null;
  }
  const matched = WEATHER_SELECT_LABELS.find((label) => normalized.includes(label));
  return matched ?? null;
}

function splitIntoChunks(content: string, maxLength: number): string[] {
  if (!content) {
    return [];
  }
  const chunks: string[] = [];
  for (let start = 0; start < content.length; start += maxLength) {
    chunks.push(content.slice(start, start + maxLength));
  }
  return chunks;
}

function splitRichText(text: string, limit = NOTES_RICH_TEXT_LIMIT) {
  return splitIntoChunks(text, limit).map((chunk) => ({
    type: "text",
    text: { content: chunk },
  }));
}

function getPlainTextFromRichText(richTextOrProperty: any): string {
  if (!richTextOrProperty) {
    return "";
  }
  const richText = Array.isArray(richTextOrProperty)
    ? richTextOrProperty
    : richTextOrProperty.rich_text;
  if (!Array.isArray(richText)) {
    return "";
  }
  return richText
    .map((item: { plain_text?: string }) => item.plain_text ?? "")
    .join("");
}

function getStringFromProperty(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const richText = getPlainTextFromRichText(property).trim();
  if (richText) {
    return richText;
  }
  const titleText = getPlainTextFromTitle(property).trim();
  if (titleText) {
    return titleText;
  }
  const selectName =
    typeof property.select?.name === "string" ? property.select.name.trim() : "";
  return selectName;
}

function getDateTimeFromProperty(property: Record<string, any> | undefined): string {
  const start =
    typeof property?.date?.start === "string" ? property.date.start.trim() : "";
  return start;
}

function getIsoStringFromProperty(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const dateValue = getDateTimeFromProperty(property);
  if (dateValue) {
    return dateValue;
  }
  const directText = getStringFromProperty(property).trim();
  if (directText) {
    return directText;
  }
  const formula = property.formula;
  if (formula) {
    if (typeof formula.string === "string" && formula.string.trim()) {
      return formula.string.trim();
    }
    if (typeof formula.number === "number") {
      return String(formula.number);
    }
    if (typeof formula.boolean === "boolean") {
      return formula.boolean ? "true" : "false";
    }
    if (typeof formula.date?.start === "string" && formula.date.start.trim()) {
      return formula.date.start.trim();
    }
  }
  return "";
}

function normalizeNote(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/[\t\f\v ]+/g, " ")
    .replace(/\n+/g, "\n")
    .trim();
}

function getPlainTextFromTitle(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const title = property.title;
  if (!Array.isArray(title)) {
    return "";
  }
  return title
    .map((item: { plain_text?: string }) => item.plain_text ?? "")
    .join("");
}

function extractMailMetadataFromProperties(properties: Record<string, any>) {
  return {
    mailInputHash:
      getPlainTextFromRichText(properties[MAIL_INPUT_HASH_PROPERTY_NAME]) || null,
    mailInputSnapshot:
      getPlainTextFromRichText(properties[MAIL_INPUT_SNAPSHOT_PROPERTY_NAME]) || null,
    mailSentAt:
      getDateTimeFromProperty(properties[MAIL_SENT_AT_PROPERTY_NAME]) ||
      getStringFromProperty(properties[MAIL_SENT_AT_PROPERTY_NAME]) ||
      null,
    mailVersion: getNumberFromProperty(properties[MAIL_VERSION_PROPERTY_NAME]),
  };
}

function buildMoodNotesEntry(notes: string, sourceUrl?: string): string {
  const trimmedNotes = notes.trim();
  let entry = "";
  if (trimmedNotes) {
    entry = trimmedNotes;
  }
  if (sourceUrl) {
    const referenceLine = `参照: ${sourceUrl}`;
    entry = entry ? `${entry}\n${referenceLine}` : referenceLine;
  }
  return entry;
}

function shouldSkipAppend(existingNotes: string, incoming: string): boolean {
  const normalizedExisting = normalizeNote(existingNotes);
  const normalizedIncoming = normalizeNote(incoming);
  if (!normalizedIncoming) {
    return true;
  }
  return (
    normalizedExisting === normalizedIncoming ||
    normalizedExisting.endsWith(normalizedIncoming)
  );
}

function getNumberFromProperty(
  property: Record<string, any> | undefined,
): number | null {
  if (!property || typeof property.number !== "number") {
    return null;
  }
  return property.number;
}

type NotionFilePropertyValue =
  | { name: string; type: "external"; external: { url: string } }
  | { name: string; type: "file"; file: { url: string } };

function normalizeFilesFromProperty(
  property: Record<string, any> | undefined,
): NotionFilePropertyValue[] {
  if (!property || !Array.isArray(property.files)) {
    return [];
  }
  return property.files
    .map((file: Record<string, any>) => {
      const name = typeof file.name === "string" ? file.name : "file";
      if (file.type === "external" && file.external?.url) {
        return { name, type: "external", external: { url: file.external.url } };
      }
      if (file.type === "file" && file.file?.url) {
        return { name, type: "file", file: { url: file.file.url } };
      }
      return null;
    })
    .filter((item): item is NotionFilePropertyValue => Boolean(item));
}

function getFileUrlsFromProperty(
  property: Record<string, any> | undefined,
): string[] {
  return normalizeFilesFromProperty(property)
    .map((file: Record<string, any>) => {
      if (file.type === "external" && file.external?.url) {
        return String(file.external.url);
      }
      if (file.type === "file" && file.file?.url) {
        return String(file.file.url);
      }
      return null;
    })
    .filter((item): item is string => Boolean(item));
}

function resolveLocationSummaryFields(
  properties: Record<string, any>,
  env: Env,
): {
  locationSummaryGpt: string | null;
  locationSummaryLegacy: string | null;
  locationSummaryPayload: string | null;
  locationSummary: string | null;
  locationSummarySource: "location_summary_gpt" | "location_summary_legacy" | "location_summary_payload" | "empty";
} {
  const configuredGptProp = env.DAILY_LOG_LOCATION_SUMMARY_PROP || "Location summary (GPT)";
  const gptPropName = resolvePropertyName(properties, configuredGptProp, "daily_log_read:location_summary_gpt");

  const exactLegacyPropName = Object.prototype.hasOwnProperty.call(properties, "Location summary")
    ? "Location summary"
    : null;
  const exactPayloadPropName = Object.prototype.hasOwnProperty.call(properties, "location_summary")
    ? "location_summary"
    : null;

  let legacyPropName = exactLegacyPropName;
  let payloadPropName = exactPayloadPropName;

  // Only fall back to normalized matching when neither exact property exists.
  // This avoids incorrectly resolving "location_summary" as legacy.
  if (!legacyPropName && !payloadPropName) {
    legacyPropName = resolvePropertyName(properties, "Location summary", "daily_log_read:location_summary_legacy");
  }
  if (!payloadPropName && !legacyPropName) {
    payloadPropName = resolvePropertyName(properties, "location_summary", "daily_log_read:location_summary_payload");
  }

  const locationSummaryGpt = (gptPropName ? getPlainTextFromRichText(properties[gptPropName]) : "").trim() || null;
  const locationSummaryLegacy = (legacyPropName ? getPlainTextFromRichText(properties[legacyPropName]) : "").trim() || null;
  const locationSummaryPayload = (payloadPropName ? getPlainTextFromRichText(properties[payloadPropName]) : "").trim() || null;
  if (locationSummaryGpt) {
    return { locationSummaryGpt, locationSummaryLegacy, locationSummaryPayload, locationSummary: locationSummaryGpt, locationSummarySource: "location_summary_gpt" };
  }
  if (locationSummaryLegacy) {
    return { locationSummaryGpt, locationSummaryLegacy, locationSummaryPayload, locationSummary: locationSummaryLegacy, locationSummarySource: "location_summary_legacy" };
  }
  if (locationSummaryPayload) {
    return { locationSummaryGpt, locationSummaryLegacy, locationSummaryPayload, locationSummary: locationSummaryPayload, locationSummarySource: "location_summary_payload" };
  }
  return { locationSummaryGpt, locationSummaryLegacy, locationSummaryPayload, locationSummary: null, locationSummarySource: "empty" };
}

function getRelationIdsFromProperty(
  property: Record<string, any> | undefined,
): string[] {
  if (!property || !Array.isArray(property.relation)) {
    return [];
  }
  return property.relation
    .map((item: { id?: string }) => item.id)
    .filter((id): id is string => Boolean(id));
}

function buildNotionPageUrl(pageId: string): string {
  return `https://www.notion.so/${pageId.replace(/-/g, "")}`;
}

async function requireBearerToken(request: Request, env: Env): Promise<Response | null> {
  if (!env.WORKERS_BEARER_TOKEN) {
    console.warn("WORKERS_BEARER_TOKEN is not set; auth is disabled");
    return null;
  }
  const authHeader = request.headers.get("authorization");
  if (!authHeader) {
    return unauthorized("missing bearer token");
  }
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (!token || token !== env.WORKERS_BEARER_TOKEN) {
    return unauthorized("invalid bearer token");
  }
  return null;
}

async function handleInbox(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return methodNotAllowed();
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  await validateDatabaseSchema(env, env.INBOX_DB_ID, INBOX_PROPERTIES);

  const response = await notionFetch(env, `/databases/${env.INBOX_DB_ID}/query`, {
    method: "POST",
    body: JSON.stringify({ page_size: 50 }),
  });
  if (!response.ok) {
    return notionErrorResponse(response, "handleInbox");
  }
  const data = await response.json();
  const results = (data.results ?? []).map((page: Record<string, any>) => ({
    id: page.id,
    title: getPageTitleFromProperty(page, TITLE_PROPERTIES.inbox),
  }));

  return new Response(JSON.stringify({ items: results }), {
    headers: jsonHeaders,
  });
}

async function handleTasks(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return methodNotAllowed();
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  const { doStatus, somedayStatus } = getTaskStatusConfig(env);
  await validateTasksDatabaseSchema(env);
  const { statusPropertyName } = getTaskPropertyNames(env);

  const response = await notionFetch(env, `/databases/${env.TASK_DB_ID}/query`, {
    method: "POST",
    body: JSON.stringify({
      page_size: 100,
      filter: {
        or: [
          { property: statusPropertyName, select: { equals: doStatus } },
          { property: statusPropertyName, select: { equals: somedayStatus } },
        ],
      },
    }),
  });

  if (!response.ok) {
    return notionErrorResponse(response, "handleTasks");
  }

  const data = await response.json();
  const origin = new URL(request.url).origin;
  const results = (data.results ?? []).map((page: Record<string, any>) => {
    const status = page.properties?.[statusPropertyName]?.select?.name ?? null;
    const someday = status === somedayStatus;
    return {
      id: page.id,
      title: getPageTitleFromProperty(page, TITLE_PROPERTIES.tasks),
      status,
      priority: page.properties?.Priority?.select?.name ?? null,
      since_do: page.properties?.["Since Do"]?.date?.start ?? null,
      someday,
      confirm_promote_url:
        someday && status !== doStatus
          ? `${origin}/confirm/tasks/promote?id=${page.id}`
          : null,
    };
  });

  return new Response(JSON.stringify({ items: results }), {
    headers: jsonHeaders,
  });
}

async function handleTasksClosed(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return methodNotAllowed();
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  await validateTasksDatabaseSchema(env);
  const { doneStatus, droppedStatus } = getTaskStatusConfig(env);
  const { statusPropertyName, doneDatePropertyName, dropDatePropertyName } =
    getTaskPropertyNames(env);

  const url = new URL(request.url);
  const dateParam = url.searchParams.get("date");
  let targetDate = dateParam?.trim();
  if (!targetDate) {
    targetDate = getJstYesterdayString();
  } else if (!isValidDateString(targetDate)) {
    return badRequest("invalid date format");
  }

  const startJst = formatJstDateTime(targetDate, "00:00:00");
  const endJst = formatJstDateTime(targetDate, "23:59:59");

  const doneFilter = {
    and: [
      { property: statusPropertyName, select: { equals: doneStatus } },
      { property: doneDatePropertyName, date: { is_not_empty: true } },
      { property: doneDatePropertyName, date: { on_or_after: startJst } },
      { property: doneDatePropertyName, date: { on_or_before: endJst } },
    ],
  };
  const dropFilter = {
    and: [
      { property: statusPropertyName, select: { equals: droppedStatus } },
      { property: dropDatePropertyName, date: { is_not_empty: true } },
      { property: dropDatePropertyName, date: { on_or_after: startJst } },
      { property: dropDatePropertyName, date: { on_or_before: endJst } },
    ],
  };

  console.log(
    `Tasks closed: target_date=${targetDate}(JST) range=${startJst}..${endJst}`,
  );
  console.log(
    `Notion query payload (tasks/closed/done): ${JSON.stringify({
      page_size: 100,
      database_id: "***",
      filter: doneFilter,
    })}`,
  );
  console.log(
    `Notion query payload (tasks/closed/drop): ${JSON.stringify({
      page_size: 100,
      database_id: "***",
      filter: dropFilter,
    })}`,
  );

  const donePages = await queryDatabaseAll(env, env.TASK_DB_ID, doneFilter);
  const dropPages = await queryDatabaseAll(env, env.TASK_DB_ID, dropFilter);

  const done = donePages
    .map((page: Record<string, any>) => {
      const doneDateRaw =
        page.properties?.[doneDatePropertyName]?.date?.start ?? null;
      const doneDateJst = doneDateRaw
        ? getJstDateStringFromDateTime(doneDateRaw)
        : null;
      return {
        page_id: page.id,
        title: getPageTitleFromProperty(page, TITLE_PROPERTIES.tasks),
        priority: page.properties?.Priority?.select?.name ?? null,
        done_date: doneDateRaw,
        event_date: page.properties?.["Event Date"]?.date?.start ?? null,
        done_date_jst: doneDateJst,
      };
    })
    .filter((item) => item.done_date && item.done_date_jst === targetDate)
    .map(({ done_date_jst, ...item }) => item);

  const drop = dropPages
    .map((page: Record<string, any>) => {
      const dropDateRaw =
        page.properties?.[dropDatePropertyName]?.date?.start ?? null;
      const dropDateJst = dropDateRaw
        ? getJstDateStringFromDateTime(dropDateRaw)
        : null;
      return {
        page_id: page.id,
        title: getPageTitleFromProperty(page, TITLE_PROPERTIES.tasks),
        priority: page.properties?.Priority?.select?.name ?? null,
        drop_date: dropDateRaw,
        drop_date_jst: dropDateJst,
      };
    })
    .filter((item) => item.drop_date && item.drop_date_jst === targetDate)
    .map(({ drop_date_jst, ...item }) => item);

  console.log(
    `Tasks closed: target_date=${targetDate} done=${done.length} drop=${drop.length}`,
  );
  for (const item of done) {
    const doneDateJst = item.done_date
      ? getJstDateStringFromDateTime(item.done_date)
      : null;
    console.log(
      `Tasks closed: item title="${item.title}" status=${doneStatus} done_date_jst=${doneDateJst}`,
    );
  }
  for (const item of drop) {
    const dropDateJst = item.drop_date
      ? getJstDateStringFromDateTime(item.drop_date)
      : null;
    console.log(
      `Tasks closed: item title="${item.title}" status=${droppedStatus} drop_date_jst=${dropDateJst}`,
    );
  }

  const debugEnabled = url.searchParams.get("debug") === "1";
  const debug = debugEnabled
    ? {
        target_date: targetDate,
        start_jst: startJst,
        end_jst: endJst,
        done_preview: done.slice(0, 5).map((item) => ({
          title: item.title,
          done_date_raw: item.done_date,
        })),
      }
    : undefined;

  return new Response(
    JSON.stringify({
      date: targetDate,
      range: {
        start_jst: startJst,
        end_jst: endJst,
      },
      done,
      drop,
      done_count: done.length,
      drop_count: drop.length,
      ...(debug ? { debug } : {}),
    }),
    { headers: jsonHeaders },
  );
}

async function handleDailyLogUpsert(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/upsert");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  console.log(
    `Daily Log schema validation uses sleep property names: ${[
      SLEEP_PROPERTY_MAPPINGS.sleepStart.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepEnd.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepScore.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepSource.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.displayName,
      SLEEP_PROPERTY_MAPPINGS.deepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.remDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessStars.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp.displayName,
      SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp.displayName,
    ].join(", ")}`
  );
  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const { data, error } = validateDailyLogPayload(payload);
  if (error) {
    return error;
  }
  if (!data) {
    return badRequest("invalid payload");
  }

  const {
    targetDate,
    title,
    summaryText,
    summaryHtml,
    mailId,
    source,
    pageId,
    updateTaskRelations,
    dataJson,
  } = data;

  let existingPage: Record<string, any> | null = null;
  let canonicalPageId: string | null = null;
  let duplicateDetected = false;
  let duplicateMergeCompleted = false;

  const resolved = await resolveDailyLogPageForDate(env, targetDate);
  existingPage = resolved.canonicalPage;
  canonicalPageId = resolved.canonicalPage?.id ?? null;
  duplicateDetected = resolved.duplicatePages.length > 0;
  duplicateMergeCompleted = resolved.mergeCompleted;

  const properties = buildDailyLogUpsertProperties({
    title,
    targetDate,
    summaryText,
    mailId,
    source,
  });

  const resolvedPageId = pageId ?? existingPage?.id;
  const { sanitizedProperties, removedEmptyMealPhotos } =
    sanitizeMealPhotosPatchProperties(properties);
  logDailyLogPatchKeys({
    endpointName: "/api/daily_log/upsert",
    targetDate,
    pageId: resolvedPageId ?? null,
    canonicalPageId,
    properties: sanitizedProperties,
    reason: removedEmptyMealPhotos ? "removed_empty_meal_photos" : "daily_log_upsert",
  });

  let resultResponse: Response;
  if (resolvedPageId) {
    resultResponse = await notionFetch(env, `/pages/${resolvedPageId}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: sanitizedProperties }),
    });
  } else {
    resultResponse = await notionFetch(env, "/pages", {
      method: "POST",
      body: JSON.stringify({
        parent: { database_id: env.DAILY_LOG_DB_ID },
        properties: sanitizedProperties,
      }),
    });
  }

  if (!resultResponse.ok) {
    const details = await getNotionErrorDetails(resultResponse);
    const requestIdLog = details.requestId ? ` request_id=${details.requestId}` : "";
    const codeLog = details.code ? ` code=${details.code}` : "";
    const messageLog = details.notionMessage ?? details.message;
    console.error(
      `Notion API error in handleDailyLogUpsert.upsert: status=${details.status}${requestIdLog}${codeLog} message=${messageLog}`,
    );
    console.error(
      `DailyLog upsert properties: ${Object.keys(properties).join(", ")}`,
    );
    return notionErrorResponseFromDetails(details);
  }

  const finalPageId = resolvedPageId ?? (await resultResponse.json()).id;
  void dataJson;

  if (updateTaskRelations) {
    await validateTasksDatabaseSchema(env);
    await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, DAILY_LOG_RELATION_PROPERTIES);

    await updateDailyLogTaskRelations(env, targetDate);
  }

  return new Response(JSON.stringify({ ok: true, page_id: finalPageId }), {
    headers: jsonHeaders,
  });
}

async function handleDailyLogHealthIngest(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/ingest_health");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }
  if (!env.HEALTH_DB_ID) {
    return new Response(JSON.stringify({ error: "missing HEALTH_DB_ID" }), {
      status: 500,
      headers: jsonHeaders,
    });
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const targetDateResult = resolveIngestTargetDate(payload);
  if (!targetDateResult.ok) {
    return badRequest(targetDateResult.reason);
  }

  const { targetDate } = targetDateResult;

  const healthPropertyNames = getHealthPropertyNames(env);
  const dailyLogHealthPropertyNames = getDailyLogHealthPropertyNames(env);
  const healthSourceValue = env.HEALTH_SOURCE_VALUE || "healthkit";

  const healthDbProperties = await getDatabaseProperties(env, env.HEALTH_DB_ID);
  if (!hasPropertyType(healthDbProperties, healthPropertyNames.date, "date")) {
    return new Response(
      JSON.stringify({
        error: "health db schema error",
        message: `Missing date property: ${healthPropertyNames.date}`,
      }),
      { status: 500, headers: jsonHeaders },
    );
  }

  const filterParts: Record<string, any>[] = [
    { property: healthPropertyNames.date, date: { equals: targetDate } },
  ];
  const hasSourceProperty = hasPropertyType(
    healthDbProperties,
    healthPropertyNames.source,
    "select",
  );
  if (hasSourceProperty) {
    filterParts.push({
      property: healthPropertyNames.source,
      select: { equals: healthSourceValue },
    });
  } else {
    console.warn(
      `Health DB missing Source select property "${healthPropertyNames.source}", skipping source filter.`,
    );
  }

  const filter = filterParts.length === 1 ? filterParts[0] : { and: filterParts };
  const queryResponse = await notionFetch(
    env,
    `/databases/${env.HEALTH_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 5,
        filter,
        sorts: [{ timestamp: "created_time", direction: "descending" }],
      }),
    },
  );

  if (!queryResponse.ok) {
    return notionErrorResponse(queryResponse, "handleDailyLogHealthIngest.queryHealth");
  }

  const queryData = await queryResponse.json();
  const healthPages = queryData.results ?? [];
  if (!healthPages.length) {
    console.log(`Health ingest: no health record for ${targetDate}`);
    return new Response(
      JSON.stringify({
        ok: true,
        target_date: targetDate,
        found: false,
        updated: false,
        reason: "no health record",
      }),
      { headers: jsonHeaders },
    );
  }

  if (healthPages.length > 1) {
    console.warn(
      `Health ingest: multiple records for ${targetDate}, using latest created_time.`,
    );
  }

  const healthPage = healthPages[0];
  const healthProps = healthPage.properties ?? {};
  const protein = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.protein, "health_ingest:protein"));
  const fat = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.fat, "health_ingest:fat"));
  const carb = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.carb, "health_ingest:carb"));
  const kcal = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.kcal, "health_ingest:kcal"));
  const weight = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.weight, "health_ingest:weight"));
  const sleepStart = getDateTimeFromProperty(getResolvedProperty(healthProps, healthPropertyNames.sleepStart, "health_ingest:sleep_start"));
  const sleepEnd = getDateTimeFromProperty(getResolvedProperty(healthProps, healthPropertyNames.sleepEnd, "health_ingest:sleep_end"));
  const sleepDurationMin = getNumberFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.sleepDurationMin, "health_ingest:sleep_duration"),
  );
  const sleepScore = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.sleepScore, "health_ingest:sleep_score"));
  const sleepSource = getStringFromProperty(getResolvedProperty(healthProps, healthPropertyNames.sleepSource, "health_ingest:sleep_source"));
  const readinessStars = getNumberFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.readinessStars, "health_ingest:readiness_stars"),
  );
  const readinessHrv = getNumberFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.readinessHrv, "health_ingest:readiness_hrv"),
  );
  const readinessBpm = getNumberFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.readinessBpm, "health_ingest:readiness_bpm"),
  );
  const baselineHrv = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.baselineHrv, "health_ingest:baseline_hrv"));
  const baselineWakingBpm = getNumberFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.baselineWakingBpm, "health_ingest:baseline_waking_bpm"),
  );
  const sleepHeartRate = getNumberFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.sleepHeartRate, "health_ingest:sleep_heart_rate"),
  );
  const deepDurationMin = getNumberFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.deepDurationMin, "health_ingest:deep_duration"),
  );
  const remDurationMin = getNumberFromProperty(getResolvedProperty(healthProps, healthPropertyNames.remDurationMin, "health_ingest:rem_duration"));
  const mealPhotos = normalizeFilesFromProperty(
    getResolvedProperty(healthProps, healthPropertyNames.mealPhoto, "health_ingest:meal_photo"),
  );
  const mealSummary = formatMealSummary(protein, fat, carb, kcal, weight);

  console.log(
    `Health ingest sleep inputs resolved for ${targetDate}: ${JSON.stringify({
      sleepStart,
      sleepEnd,
      sleepDurationMin,
      sleepScore,
      sleepSource,
      sleepHeartRate,
      deepDurationMin,
      remDurationMin,
      readinessStars,
      readinessHrv,
      readinessBpm,
      baselineHrv,
      baselineWakingBpm,
    })}`
  );

  console.log(
    `Daily Log schema validation uses sleep property names: ${[
      SLEEP_PROPERTY_MAPPINGS.sleepStart.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepEnd.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepScore.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepSource.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.displayName,
      SLEEP_PROPERTY_MAPPINGS.deepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.remDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessStars.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp.displayName,
      SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp.displayName,
    ].join(", ")}`
  );
  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));
  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  const resolvedDailyLog = await resolveDailyLogPageForDate(env, targetDate);
  const existingPage = resolvedDailyLog.canonicalPage;
  const updateProperties: Record<string, any> = {};

  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.protein, "number")) {
    updateProperties[dailyLogHealthPropertyNames.protein] =
      createNumberProperty(protein);
  } else {
    console.warn(
      `Daily_Log missing number property "${dailyLogHealthPropertyNames.protein}", skipping.`,
    );
  }
  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.fat, "number")) {
    updateProperties[dailyLogHealthPropertyNames.fat] = createNumberProperty(fat);
  } else {
    console.warn(
      `Daily_Log missing number property "${dailyLogHealthPropertyNames.fat}", skipping.`,
    );
  }
  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.carb, "number")) {
    updateProperties[dailyLogHealthPropertyNames.carb] =
      createNumberProperty(carb);
  } else {
    console.warn(
      `Daily_Log missing number property "${dailyLogHealthPropertyNames.carb}", skipping.`,
    );
  }
  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.kcal, "number")) {
    updateProperties[dailyLogHealthPropertyNames.kcal] =
      createNumberProperty(kcal);
  } else {
    console.warn(
      `Daily_Log missing number property "${dailyLogHealthPropertyNames.kcal}", skipping.`,
    );
  }
  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.weight, "number")) {
    updateProperties[dailyLogHealthPropertyNames.weight] =
      createNumberProperty(weight);
  } else {
    console.warn(
      `Daily_Log missing number property "${dailyLogHealthPropertyNames.weight}", skipping.`,
    );
  }
  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.mealPhoto, "files")) {
    const existingMealPhotos = normalizeFilesFromProperty(
      existingPage?.properties?.[dailyLogHealthPropertyNames.mealPhoto],
    );
    const mergedMealPhotos = mergeNotionFilesDedup(existingMealPhotos, mealPhotos);
    if (mealPhotos.length > 0 && mergedMealPhotos.length > 0) {
      updateProperties[dailyLogHealthPropertyNames.mealPhoto] =
        createFilesProperty(mergedMealPhotos);
    }
  } else {
    console.warn(
      `Daily_Log missing files property "${dailyLogHealthPropertyNames.mealPhoto}", skipping.`,
    );
  }
  if (hasPropertyType(dailyLogProperties, "Meal summary", "rich_text")) {
    updateProperties["Meal summary"] = createRichTextProperty(mealSummary);
  } else {
    console.warn('Daily_Log missing rich_text property "Meal summary", skipping.');
  }
  const sleepStartPropertyName = canUseProperty(
    dailyLogProperties,
    dailyLogHealthPropertyNames.sleepStart,
    "date",
    "health_ingest:daily_log_sleep_start",
  );
  if (hasNonEmptyValue(sleepStart) && sleepStartPropertyName) {
    updateProperties[sleepStartPropertyName] = createDateProperty(sleepStart as string);
  }
  const sleepEndPropertyName = canUseProperty(
    dailyLogProperties,
    dailyLogHealthPropertyNames.sleepEnd,
    "date",
    "health_ingest:daily_log_sleep_end",
  );
  if (hasNonEmptyValue(sleepEnd) && sleepEndPropertyName) {
    updateProperties[sleepEndPropertyName] = createDateProperty(sleepEnd as string);
  }
  const optionalNumberFields: Array<[string, number | null | undefined]> = [
    [dailyLogHealthPropertyNames.sleepDurationMin, sleepDurationMin],
    [dailyLogHealthPropertyNames.sleepScore, sleepScore],
    [dailyLogHealthPropertyNames.readinessStars, readinessStars],
    [dailyLogHealthPropertyNames.readinessHrv, readinessHrv],
    [dailyLogHealthPropertyNames.readinessBpm, readinessBpm],
    [dailyLogHealthPropertyNames.baselineHrv, baselineHrv],
    [dailyLogHealthPropertyNames.baselineWakingBpm, baselineWakingBpm],
    [dailyLogHealthPropertyNames.sleepHeartRate, sleepHeartRate],
    [dailyLogHealthPropertyNames.deepDurationMin, deepDurationMin],
    [dailyLogHealthPropertyNames.remDurationMin, remDurationMin],
  ];
  for (const [propertyName, value] of optionalNumberFields) {
    if (!hasNonEmptyValue(value)) {
      continue;
    }
    const resolvedPropertyName = canUseProperty(
      dailyLogProperties,
      propertyName,
      "number",
      `health_ingest:${propertyName}`,
    );
    if (!resolvedPropertyName) {
      continue;
    }
    updateProperties[resolvedPropertyName] = createNumberProperty(value);
  }
  if (hasNonEmptyValue(sleepSource)) {
    const sleepSourceRichTextPropertyName = canUseProperty(
      dailyLogProperties,
      dailyLogHealthPropertyNames.sleepSource,
      "rich_text",
      "health_ingest:daily_log_sleep_source_rich_text",
    );
    const sleepSourceSelectPropertyName = canUseProperty(
      dailyLogProperties,
      dailyLogHealthPropertyNames.sleepSource,
      "select",
      "health_ingest:daily_log_sleep_source_select",
    );
    if (sleepSourceRichTextPropertyName) {
      updateProperties[sleepSourceRichTextPropertyName] = createRichTextProperty(
        sleepSource as string,
      );
    } else if (sleepSourceSelectPropertyName) {
      updateProperties[sleepSourceSelectPropertyName] = createSelectProperty(
        sleepSource as string,
      );
    }
  }

  if (!Object.keys(updateProperties).length) {
    return new Response(
      JSON.stringify({
        ok: true,
        target_date: targetDate,
        found: true,
        updated: false,
        reason: "no updatable properties",
        health_page_id: healthPage.id,
      }),
      { headers: jsonHeaders },
    );
  }

  const { sanitizedProperties, removedEmptyMealPhotos } =
    sanitizeMealPhotosPatchProperties(updateProperties);
  logDailyLogPatchKeys({
    endpointName: "/execute/api/daily_log/ingest_health",
    targetDate,
    pageId: existingPage?.id ?? null,
    canonicalPageId: existingPage?.id ?? null,
    properties: sanitizedProperties,
    reason: removedEmptyMealPhotos ? "removed_empty_meal_photos" : "health_ingest_update",
  });

  let resultResponse: Response;
  if (existingPage) {
    resultResponse = await notionFetch(env, `/pages/${existingPage.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: sanitizedProperties }),
    });
  } else {
    const title = `Daily Log｜${targetDate}`;
    const properties = {
      [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
      "Target Date": createDateProperty(targetDate),
      Date: createDateProperty(targetDate),
      ...sanitizedProperties,
    };
    resultResponse = await notionFetch(env, "/pages", {
      method: "POST",
      body: JSON.stringify({
        parent: { database_id: env.DAILY_LOG_DB_ID },
        properties,
      }),
    });
  }

  if (!resultResponse.ok) {
    const details = await getNotionErrorDetails(resultResponse);
    const requestIdLog = details.requestId ? ` request_id=${details.requestId}` : "";
    const codeLog = details.code ? ` code=${details.code}` : "";
    const messageLog = details.notionMessage ?? details.message;
    console.error(
      `Notion API error in handleDailyLogHealthIngest.upsert: status=${details.status}${requestIdLog}${codeLog} message=${messageLog}`,
    );
    console.error(
      `DailyLog health upsert properties: ${Object.keys(updateProperties).join(", ")}`,
    );
    return notionErrorResponseFromDetails(details);
  }

  const pageId = existingPage ? existingPage.id : (await resultResponse.json()).id;
  return new Response(
    JSON.stringify({
      ok: true,
      target_date: targetDate,
      found: true,
      updated: true,
      page_id: pageId,
      health_page_id: healthPage.id,
    }),
    { headers: jsonHeaders },
  );
}

async function upsertDailyLogByTargetDate(
  env: Env,
  targetDate: string,
  updateProperties: Record<string, any>,
  logContext: string,
): Promise<{ pageId: string; duplicateInfo: any } | { error: Response }> {
  const resolvedDailyLog = await resolveDailyLogPageForDate(env, targetDate);
  const existingPage = resolvedDailyLog.canonicalPage;

  const { sanitizedProperties, removedEmptyMealPhotos } =
    sanitizeMealPhotosPatchProperties(updateProperties);
  logDailyLogPatchKeys({
    endpointName: logContext,
    targetDate,
    pageId: existingPage?.id ?? null,
    canonicalPageId: existingPage?.id ?? null,
    properties: sanitizedProperties,
    reason: removedEmptyMealPhotos ? "removed_empty_meal_photos" : `${logContext}_update`,
  });

  let resultResponse: Response;
  if (existingPage) {
    resultResponse = await notionFetch(env, `/pages/${existingPage.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: sanitizedProperties }),
    });
  } else {
    const title = `Daily Log｜${targetDate}`;
    const properties = {
      [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
      "Target Date": createDateProperty(targetDate),
      Date: createDateProperty(targetDate),
      ...sanitizedProperties,
    };
    resultResponse = await notionFetch(env, "/pages", {
      method: "POST",
      body: JSON.stringify({
        parent: { database_id: env.DAILY_LOG_DB_ID },
        properties,
      }),
    });
  }

  if (!resultResponse.ok) {
    const details = await getNotionErrorDetails(resultResponse);
    const requestIdLog = details.requestId ? ` request_id=${details.requestId}` : "";
    const codeLog = details.code ? ` code=${details.code}` : "";
    const messageLog = details.notionMessage ?? details.message;
    console.error(
      `Notion API error in ${logContext}.upsert: status=${details.status}${requestIdLog}${codeLog} message=${messageLog}`,
    );
    console.error(
      `DailyLog upsert properties (${logContext}): ${Object.keys(updateProperties).join(", ")}`,
    );
    return { error: notionErrorResponseFromDetails(details) };
  }

  const pageId = existingPage ? existingPage.id : (await resultResponse.json()).id;
  return { pageId, duplicateInfo: buildDuplicateInfo(resolvedDailyLog, pageId) };
}

async function handleDailyLogPhotosIngest(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/ingest_photos");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }
  if (!env.HEALTH_DB_ID) {
    return new Response(JSON.stringify({ error: "missing HEALTH_DB_ID" }), {
      status: 500,
      headers: jsonHeaders,
    });
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const targetDateResult = resolveIngestTargetDate(payload);
  if (!targetDateResult.ok) {
    return badRequest(targetDateResult.reason);
  }
  const { targetDate } = targetDateResult;
  console.info(`[daily_log.ingest_photos] target_date=${targetDate}`);

  const healthPropertyNames = getHealthPropertyNames(env);
  const dailyLogHealthPropertyNames = getDailyLogHealthPropertyNames(env);
  const healthDbProperties = await getDatabaseProperties(env, env.HEALTH_DB_ID);
  const configuredHealthMealPhotoPropertyName = healthPropertyNames.mealPhoto;
  const healthMealPhotoPropertyNameCandidates = [
    configuredHealthMealPhotoPropertyName,
    "Meal Photos",
    "Meal photo",
  ].filter((name, index, self) => Boolean(name) && self.indexOf(name) === index);
  const healthMealPhotoPropertyName =
    healthMealPhotoPropertyNameCandidates.find(
      (name) => healthDbProperties[name]?.type === "files",
    ) ?? configuredHealthMealPhotoPropertyName;
  console.info(
    `[daily_log.ingest_photos] health_meal_photo_property_name=${healthMealPhotoPropertyName} configured=${configuredHealthMealPhotoPropertyName}`,
  );
  if (!hasPropertyType(healthDbProperties, healthPropertyNames.date, "date")) {
    return new Response(
      JSON.stringify({
        error: "health db schema error",
        message: `Missing date property: ${healthPropertyNames.date}`,
      }),
      { status: 500, headers: jsonHeaders },
    );
  }

  const queryResponse = await notionFetch(
    env,
    `/databases/${env.HEALTH_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 100,
        filter: { property: healthPropertyNames.date, date: { equals: targetDate } },
        sorts: [{ timestamp: "created_time", direction: "descending" }],
      }),
    },
  );
  if (!queryResponse.ok) {
    return notionErrorResponse(queryResponse, "handleDailyLogPhotosIngest.queryHealth");
  }

  const queryData = await queryResponse.json();
  const healthPages = queryData.results ?? [];
  console.info(`[daily_log.ingest_photos] health_pages_count=${healthPages.length}`);

  for (const page of healthPages) {
    const pageId = typeof page?.id === "string" ? page.id : "unknown";
    const mealPhotoProperty = page?.properties?.[healthMealPhotoPropertyName];
    const propertyExists = Boolean(mealPhotoProperty);
    const propertyType =
      propertyExists && typeof mealPhotoProperty.type === "string"
        ? mealPhotoProperty.type
        : "unknown";
    const normalizedPhotoCount = normalizeFilesFromProperty(mealPhotoProperty).length;
    console.info(
      `[daily_log.ingest_photos] page_id=${pageId} property_exists=${propertyExists} property_type=${propertyType} normalized_photo_count=${normalizedPhotoCount}`,
    );
  }

  const mealPhotos = collectMealPhotosFromHealthPages(
    healthPages,
    healthMealPhotoPropertyName,
    normalizeFilesFromProperty,
  );
  console.info(`[daily_log.ingest_photos] collected_meal_photos_count=${mealPhotos.length}`);
  const resolvedDailyLog = await resolveDailyLogPageForDate(env, targetDate);
  const existingDailyLogMealPhotos = normalizeFilesFromProperty(
    resolvedDailyLog.canonicalPage?.properties?.[dailyLogHealthPropertyNames.mealPhoto],
  );
  if (!mealPhotos.length) {
    const noPhotosReason =
      healthPages.length === 0
        ? "pages_count=0"
        : healthPages.some(
              (page: Record<string, any>) =>
                !page?.properties?.[healthMealPhotoPropertyName],
            )
          ? "property_missing"
          : "normalized_photo_count=0";
    console.warn(`[daily_log.ingest_photos] no_photos_reason=${noPhotosReason}`);
    console.info(
      `[daily_log.ingest_photos] DAILY_LOG_INGEST_PHOTOS_SKIP_EMPTY target_date=${targetDate} health_photos_count=${mealPhotos.length} daily_log_existing_photos_count=${existingDailyLogMealPhotos.length} reason=health_photos_empty_keep_existing_daily_log_photos`,
    );
    return new Response(
      JSON.stringify({
        ok: true,
        target_date: targetDate,
        found: false,
        updated: false,
        reason: "no photos",
        details: noPhotosReason,
      }),
      { headers: jsonHeaders },
    );
  }

  console.log(
    `Daily Log schema validation uses sleep property names: ${[
      SLEEP_PROPERTY_MAPPINGS.sleepStart.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepEnd.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepScore.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepSource.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.displayName,
      SLEEP_PROPERTY_MAPPINGS.deepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.remDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessStars.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp.displayName,
      SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp.displayName,
    ].join(", ")}`
  );
  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));
  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  let updateProperties: Record<string, any> = {};
  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.mealPhoto, "files")) {
    const rawPhotoUpdate = buildMealPhotoUpdateProperties(
      dailyLogHealthPropertyNames.mealPhoto,
      existingDailyLogMealPhotos,
      mealPhotos,
    );
    if (rawPhotoUpdate[dailyLogHealthPropertyNames.mealPhoto]?.files) {
      updateProperties = buildPhotoOnlyUpdateProperties(
        dailyLogHealthPropertyNames.mealPhoto,
        createFilesProperty(rawPhotoUpdate[dailyLogHealthPropertyNames.mealPhoto].files),
      );
    }
  } else {
    console.warn(
      `Daily_Log missing files property "${dailyLogHealthPropertyNames.mealPhoto}", skipping.`,
    );
  }

  if (!Object.keys(updateProperties).length) {
    return new Response(
      JSON.stringify({
        ok: true,
        target_date: targetDate,
        found: true,
        updated: false,
        reason: "no updatable properties",
      }),
      { headers: jsonHeaders },
    );
  }

  const upsertResult = await upsertDailyLogByTargetDate(
    env,
    targetDate,
    updateProperties,
    "handleDailyLogPhotosIngest",
  );
  if ("error" in upsertResult) {
    return upsertResult.error;
  }
  console.info(`[daily_log.ingest_photos] daily_log_page_id=${upsertResult.pageId}`);

  return new Response(
    JSON.stringify({
      ok: true,
      target_date: targetDate,
      found: true,
      updated: true,
      page_id: upsertResult.pageId,
    }),
    { headers: jsonHeaders },
  );
}



function getNumberLikeFromProperty(property: Record<string, any> | undefined): number | null {
  const num = getNumberFromProperty(property);
  if (typeof num === "number") {
    return num;
  }
  const text = getStringFromProperty(property).trim();
  if (!text) {
    return null;
  }
  const parsed = Number.parseFloat(text);
  return Number.isFinite(parsed) ? parsed : null;
}

async function queryDatabaseAllWithBody(
  env: Env,
  dbId: string,
  body: Record<string, any>,
): Promise<Record<string, any>[]> {
  const results: Record<string, any>[] = [];
  let hasMore = true;
  let startCursor: string | undefined;

  while (hasMore) {
    const response = await notionFetch(env, `/databases/${dbId}/query`, {
      method: "POST",
      body: JSON.stringify({
        page_size: 100,
        ...body,
        ...(startCursor ? { start_cursor: startCursor } : {}),
      }),
    });
    if (!response.ok) {
      const details = await getNotionErrorDetails(response);
      throw new NotionApiError(details);
    }
    const data = await response.json();
    results.push(...(data.results ?? []));
    hasMore = data.has_more ?? false;
    startCursor = data.next_cursor ?? undefined;
  }

  return results;
}

type LocationGptOutput = {
  location_summary_text?: string;
  primary_place_label?: string;
  stats?: Record<string, any>;
};

async function generateLocationSummaryWithGpt(
  env: Env,
  diaryDate: string,
  windowStart: string,
  windowEnd: string,
  segments: LocationSegment[],
  moveCount: number,
  dataQualityNotes: string[],
): Promise<LocationSummaryResult> {
  const fallback = buildFallbackLocationSummary(
    windowStart,
    windowEnd,
    segments,
    moveCount,
    dataQualityNotes,
  );
  if (!env.OPENAI_API_KEY) {
    console.warn("Location summary: OPENAI_API_KEY is missing, using fallback text.");
    return fallback;
  }

  const model = env.OPENAI_MODEL || "gpt-4.1-mini";
  const baseUrl = env.OPENAI_BASE_URL || "https://api.openai.com/v1";
  const systemPrompt = `あなたは位置ログから「日記風の行動記録」を作成するアシスタントです。
入力に含まれる時刻と場所（住所文字列/緯度経度）だけを根拠に書いてください。

禁止:
- 店名・施設名・目的・同行者・活動内容の推測（例: “食事した”“打ち合わせした”など）
- “たぶん/おそらく/〜と思う” などの推測表現
- 入力にない地名の追加

許可:
- 時間帯、移動、滞在の事実整理
- 住所文字列からの短縮（ただし元の文字列に含まれる語だけで短縮）

出力は必ずJSONのみ。`;

  const userPrompt = `日記対象日: ${diaryDate}
対象時間窓（JST）:
- start: ${windowStart}
- end: ${windowEnd}

以下は同一地点をまとめた滞在セグメントです（JST）。
これを元に、Notionに貼れる「日記風のLocation summary」を作ってください。

入力:
${JSON.stringify(
    {
      window_start: windowStart,
      window_end: windowEnd,
      diary_date: diaryDate,
      move_count: moveCount,
      segments,
    },
    null,
    2,
  )}

出力JSON（厳守）:
{
  "location_summary_text": "（日本語の日記風。Notionにそのまま貼る）",
  "primary_place_label": "（最長滞在の短縮ラベル、無ければ空）",
  "stats": {
    "window_start": "...",
    "window_end": "...",
    "move_count": 0,
    "first_seen": "HH:MM",
    "last_seen": "HH:MM",
    "top_places": [
      { "place_label": "...", "duration_min": 0, "visits": 0 }
    ],
    "data_quality_notes": []
  }
}

location_summary_text の書式ルール:
- 冒頭に「（前日05:00〜当日05:00）」の対象時間窓を1行で書く
- その後、2〜6行程度の“日記風”文章
- 最後に「タイムライン:」として箇条書きでセグメントを列挙
- 活動内容は書かない（場所と移動だけ）`;

  const response = await fetch(`${baseUrl.replace(/\/$/, "")}/chat/completions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${env.OPENAI_API_KEY}`,
    },
    body: JSON.stringify({
      model,
      temperature: 0.2,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userPrompt },
      ],
    }),
  });

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    console.warn(
      `Location summary GPT failed status=${response.status} body=${body.slice(0, 500)}`,
    );
    return fallback;
  }

  // OpenAIの返却はJSONのはずだが、念のため text でも受けられるようにする
  let data: any;
  try {
    data = await response.json();
  } catch (error) {
    const body = await response.text().catch(() => "");
    console.warn(
      `Location summary GPT returned non-JSON response. body=${body.slice(0, 500)}`,
      error,
    );
    return fallback;
  }

  const content = data?.choices?.[0]?.message?.content;
  if (typeof content !== "string" || !content.trim()) {
    console.warn("Location summary GPT response missing message.content");
    return fallback;
  }

  let parsed: any;
  try {
    parsed = JSON.parse(content);
  } catch (error) {
    console.warn(
      `Location summary: failed to parse GPT JSON content. content=${content.slice(0, 500)}`,
      error,
    );
    return fallback;
  }

  const textRaw = parsed?.location_summary_text;
  const text = typeof textRaw === "string" ? textRaw.trim() : "";
  if (!text) {
    console.warn("Location summary: parsed JSON missing location_summary_text");
    return fallback;
  }

  const primaryRaw = parsed?.primary_place_label;
  const primary =
    typeof primaryRaw === "string" && primaryRaw.trim()
      ? primaryRaw.trim()
      : fallback.primary_place_label;

  const statsRaw = parsed?.stats;
  const stats =
    statsRaw && typeof statsRaw === "object" ? statsRaw : fallback.stats;

  return {
    location_summary_text: text,
    primary_place_label: primary,
    stats: {
      ...fallback.stats,
      ...stats,
      data_quality_notes: dataQualityNotes,
    },
  } as LocationSummaryResult;
}
function normalizeLocationLogPage(
  page: Record<string, any>,
  propertyNames: LocationPropertyNames,
): NormalizedLocationLog | null {
  const properties = page.properties ?? {};
  const timeIso = getDateTimeFromProperty(properties[propertyNames.time]);
  const timeMs = Date.parse(timeIso);
  if (!timeIso || Number.isNaN(timeMs)) {
    return null;
  }

  return {
    timeIso,
    timeMs,
    place: getStringFromProperty(properties[propertyNames.place]).trim(),
    lat: getNumberLikeFromProperty(properties[propertyNames.lat]),
    lon: getNumberLikeFromProperty(properties[propertyNames.lon]),
    source: getStringFromProperty(properties[propertyNames.source]).trim(),
  };
}

async function handleDailyLogLocationIngest(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/ingest_location");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }
  if (!env.LOCATION_LOG_DB_ID) {
    return badRequest("LOCATION_LOG_DB_ID is not set");
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const targetDateResult = resolveIngestTargetDate(payload);
  if (!targetDateResult.ok) {
    return badRequest(targetDateResult.reason);
  }

  const diaryDate = targetDateResult.targetDate;
  const windowStartHour = parseIntEnv(env.WINDOW_START_HOUR, 5);
  const previousDate = addDaysToJstDate(diaryDate, -1);
  const hourText = String(windowStartHour).padStart(2, "0");
  const window = {
    anchorStartIso: `${previousDate}T${hourText}:00:00+09:00`,
    anchorEndIso: `${diaryDate}T${hourText}:00:00+09:00`,
    diaryDate,
  };
  const locationPropertyNames = getLocationPropertyNames(env);
  const dailyLogDateProp = getDailyLogDatePropertyName(env);

  const locationPages = await queryDatabaseAllWithBody(env, env.LOCATION_LOG_DB_ID, {
    filter: {
      and: [
        { property: locationPropertyNames.time, date: { on_or_after: window.anchorStartIso } },
        { property: locationPropertyNames.time, date: { before: window.anchorEndIso } },
      ],
    },
    sorts: [{ property: locationPropertyNames.time, direction: "ascending" }],
  });

  const normalized = locationPages
    .map((page) => normalizeLocationLogPage(page, locationPropertyNames))
    .filter((item): item is NormalizedLocationLog => Boolean(item));
  const { segments, moveCount } = segmentLocationLogs(
    normalized,
    parseIntEnv(env.LOCATION_ROUND_DECIMALS, 4),
    5,
  );
  const dataQualityNotes: string[] = [];
  if (!normalized.length) {
    dataQualityNotes.push("位置ログなし");
  }
  const summary = await generateLocationSummaryWithGpt(
    env,
    diaryDate,
    window.anchorStartIso,
    window.anchorEndIso,
    segments,
    moveCount,
    dataQualityNotes,
  );

  const dailyLogPages = await queryDatabaseAllWithBody(env, env.DAILY_LOG_DB_ID, {
    filter: {
      property: dailyLogDateProp,
      date: { equals: diaryDate },
    },
    sorts: [{ timestamp: "last_edited_time", direction: "descending" }],
    page_size: 1,
  });

  let pageId = dailyLogPages[0]?.id as string | undefined;
  if (!pageId) {
    const upsertResult = await upsertDailyLogByTargetDate(
      env,
      diaryDate,
      {},
      "handleDailyLogLocationIngest",
    );
    if ("error" in upsertResult) {
      return upsertResult.error;
    }
    pageId = upsertResult.pageId;
  }

  if (parseBooleanEnvWithDefault(env.DRY_RUN, false)) {
    return new Response(
      JSON.stringify({
        ok: true,
        dry_run: true,
        target_date: diaryDate,
        window_start: window.anchorStartIso,
        window_end: window.anchorEndIso,
        location_logs: normalized.length,
        segment_count: segments.length,
        move_count: moveCount,
        page_id: pageId,
        location_summary_text: summary.location_summary_text,
        stats: summary.stats,
      }),
      { headers: jsonHeaders },
    );
  }

  const locationSummaryProp = env.DAILY_LOG_LOCATION_SUMMARY_PROP || "Location summary (GPT)";
  const updateResponse = await notionFetch(env, `/pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify({
      properties: {
        [locationSummaryProp]: createRichTextProperty(summary.location_summary_text),
      },
    }),
  });
  if (!updateResponse.ok) {
    return notionErrorResponse(updateResponse, "handleDailyLogLocationIngest.updateDailyLog");
  }

  return new Response(
    JSON.stringify({
      ok: true,
      target_date: diaryDate,
      window_start: window.anchorStartIso,
      window_end: window.anchorEndIso,
      location_logs: normalized.length,
      segment_count: segments.length,
      move_count: moveCount,
      page_id: pageId,
      stats: summary.stats,
    }),
    { headers: jsonHeaders },
  );
}

async function handleDailyLogIngest(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/ingest_daily_log");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }
  const targetDateResult = resolveIngestTargetDate(payload);
  if (!targetDateResult.ok) {
    return badRequest(targetDateResult.reason);
  }
  const { targetDate } = targetDateResult;
  const ingestPayload = { target_date: targetDate };

  const commonHeaders = new Headers({ "content-type": "application/json" });
  const authorization = request.headers.get("authorization");
  if (authorization) {
    commonHeaders.set("authorization", authorization);
  }

  const healthRequest = new Request(request.url, {
    method: "POST",
    headers: commonHeaders,
    body: JSON.stringify(ingestPayload),
  });
  const photosRequest = new Request(request.url, {
    method: "POST",
    headers: commonHeaders,
    body: JSON.stringify(ingestPayload),
  });
  const locationRequest = new Request(request.url, {
    method: "POST",
    headers: commonHeaders,
    body: JSON.stringify(ingestPayload),
  });

  const healthResponse = await handleDailyLogHealthIngest(healthRequest, env);
  const photosResponse = await handleDailyLogPhotosIngest(photosRequest, env);
  const locationResponse = await handleDailyLogLocationIngest(locationRequest, env);
  const health = await healthResponse.json();
  const photos = await photosResponse.json();
  const location = await locationResponse.json();

  return new Response(
    JSON.stringify({
      ok: true,
      target_date: targetDate,
      health,
      photos,
      location,
    }),
    { headers: jsonHeaders },
  );
}

async function handleDailyLogExpensesIngest(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/ingest_expenses");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }
  if (!env.EXPENSES_DB_ID) {
    return new Response(JSON.stringify({ error: "missing EXPENSES_DB_ID" }), {
      status: 500,
      headers: jsonHeaders,
    });
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const targetDateResult = resolveIngestTargetDate(payload);
  if (!targetDateResult.ok) {
    return badRequest(targetDateResult.reason);
  }

  const targetDate = targetDateResult.targetDate;
  const expensesDayStartHour = getExpensesDayStartHour(env);
  const expensesWindow = resolveExpensesAggregationWindow(
    targetDate,
    expensesDayStartHour,
  );

  const expensesPropertyNames = getExpensesPropertyNames(env);
  const dailyLogExpensesPropertyNames = getDailyLogExpensesPropertyNames(env);

  const { startJst, endJst } = expensesWindow;
  const filter = {
    and: [
      {
        property: expensesPropertyNames.date,
        date: { on_or_after: startJst },
      },
      {
        property: expensesPropertyNames.date,
        date: { before: endJst },
      },
    ],
  };

  console.log(`INFO: expense aggregation window start_jst=${startJst} end_jst=${endJst}`);

  let expensePages: Record<string, any>[] = [];
  try {
    expensePages = await queryDatabaseAll(env, env.EXPENSES_DB_ID, filter);
  } catch (error) {
    if (error instanceof NotionApiError) {
      return notionErrorResponseFromDetails(error);
    }
    throw error;
  }

  const startMs = Date.parse(startJst);
  const endMs = Date.parse(endJst);
  const filteredExpensePages = expensePages.filter((page) => {
    if (isFamilyCardExpense(page)) {
      return false;
    }
    const timestampMs = parseExpenseTimestampMs(page, expensesPropertyNames.date);
    return Number.isFinite(timestampMs) && timestampMs >= startMs && timestampMs < endMs;
  });

  const sampleCreatedTimeLogs = expensePages.slice(0, 3).map((page) => {
    const rawCreatedTime = typeof page.created_time === "string" ? page.created_time : "";
    const createdTimeMs = parseExpenseCreatedTimeMs(page);
    return {
      id: page.id ?? null,
      created_time: rawCreatedTime || null,
      created_time_jst: formatDebugJstDateTime(createdTimeMs),
    };
  });

  console.log(
    `INFO: expenses fetched_count=${expensePages.length} filtered_count=${filteredExpensePages.length} sample_created_time=${JSON.stringify(sampleCreatedTimeLogs)}`,
  );

  const pageIds = filteredExpensePages.map((page) => page.id).filter(Boolean);
  const total = filteredExpensePages.reduce((acc, page) => {
    const amount = getNumberFromProperty(
      page.properties?.[expensesPropertyNames.amount],
    );
    return acc + (amount ?? 0);
  }, 0);

  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  const updateProperties: Record<string, any> = {};

  if (
    hasPropertyType(
      dailyLogProperties,
      dailyLogExpensesPropertyNames.total,
      "number",
    )
  ) {
    updateProperties[dailyLogExpensesPropertyNames.total] = createNumberProperty(total);
  } else {
    console.warn(
      `Daily_Log missing number property "${dailyLogExpensesPropertyNames.total}", skipping.`,
    );
  }

  if (
    hasPropertyType(
      dailyLogProperties,
      dailyLogExpensesPropertyNames.relation,
      "relation",
    )
  ) {
    updateProperties[dailyLogExpensesPropertyNames.relation] =
      createRelationProperty(pageIds);
  } else {
    console.warn(
      `Daily_Log missing relation property "${dailyLogExpensesPropertyNames.relation}", skipping.`,
    );
  }

  if (!Object.keys(updateProperties).length) {
    return new Response(
      JSON.stringify({
        ok: true,
        target_date: targetDate,
        found: true,
        updated: false,
        reason: "no updatable properties",
        expenses_count: pageIds.length,
        expenses_total: total,
      }),
      { headers: jsonHeaders },
    );
  }

  const resolvedDailyLog = await resolveDailyLogPageForDate(env, targetDate);
  const existingPage = resolvedDailyLog.canonicalPage;
  const { sanitizedProperties, removedEmptyMealPhotos } =
    sanitizeMealPhotosPatchProperties(updateProperties);
  logDailyLogPatchKeys({
    endpointName: "/execute/api/daily_log/ingest_expenses",
    targetDate,
    pageId: existingPage?.id ?? null,
    canonicalPageId: existingPage?.id ?? null,
    properties: sanitizedProperties,
    reason: removedEmptyMealPhotos ? "removed_empty_meal_photos" : "expenses_ingest_update",
  });

  let resultResponse: Response;
  if (existingPage) {
    resultResponse = await notionFetch(env, `/pages/${existingPage.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: sanitizedProperties }),
    });
  } else {
    const title = `Daily Log｜${targetDate}`;
    const properties = {
      [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
      "Target Date": createDateProperty(targetDate),
      Date: createDateProperty(targetDate),
      ...sanitizedProperties,
    };
    resultResponse = await notionFetch(env, "/pages", {
      method: "POST",
      body: JSON.stringify({
        parent: { database_id: env.DAILY_LOG_DB_ID },
        properties,
      }),
    });
  }

  if (!resultResponse.ok) {
    const details = await getNotionErrorDetails(resultResponse);
    const requestIdLog = details.requestId ? ` request_id=${details.requestId}` : "";
    const codeLog = details.code ? ` code=${details.code}` : "";
    const messageLog = details.notionMessage ?? details.message;
    console.error(
      `Notion API error in handleDailyLogExpensesIngest.upsert: status=${details.status}${requestIdLog}${codeLog} message=${messageLog}`,
    );
    console.error(
      `DailyLog expenses upsert properties: ${Object.keys(updateProperties).join(
        ", ",
      )}`,
    );
    return notionErrorResponseFromDetails(details);
  }

  const pageId = existingPage ? existingPage.id : (await resultResponse.json()).id;
  return new Response(
    JSON.stringify({
      ok: true,
      target_date: targetDate,
      found: true,
      updated: true,
      page_id: pageId,
      expenses_count: pageIds.length,
      expenses_total: total,
      expenses_page_ids: pageIds,
    }),
    { headers: jsonHeaders },
  );
}

function getDateStartFromProperty(property: Record<string, any> | undefined): string {
  const start = property?.date?.start;
  return typeof start === "string" ? start : "";
}

function getCheckboxFromProperty(property: Record<string, any> | undefined): boolean | null {
  if (!property || typeof property.checkbox !== "boolean") {
    return null;
  }
  return property.checkbox;
}

type DailyLogTaskDetail = {
  page_id: string;
  title: string;
  done_date: string | null;
  event_date: string | null;
};

async function readTaskDetailsByRelationIds(
  env: Env,
  pageIds: string[],
): Promise<DailyLogTaskDetail[]> {
  if (!pageIds.length) {
    return [];
  }

  const { doneDatePropertyName } = getTaskPropertyNames(env);
  const details = await Promise.all(
    pageIds.map(async (pageId) => {
      const response = await notionFetch(env, `/pages/${pageId}`);
      if (!response.ok) {
        const errorDetails = await getNotionErrorDetails(response);
        console.warn(
          `Notion API error in readTaskDetailsByRelationIds: status=${errorDetails.status} page_id=${pageId} message=${errorDetails.message}`,
        );
        return null;
      }
      const page = await response.json();
      return {
        page_id: pageId,
        title: getPageTitleFromProperty(page, TITLE_PROPERTIES.tasks).trim(),
        done_date: page.properties?.[doneDatePropertyName]?.date?.start ?? null,
        event_date: page.properties?.["Event Date"]?.date?.start ?? null,
      } satisfies DailyLogTaskDetail;
    }),
  );

  return details.filter((item): item is DailyLogTaskDetail => Boolean(item?.title));
}

async function readTaskTitlesByRelationIds(
  env: Env,
  pageIds: string[],
): Promise<string[]> {
  const details = await readTaskDetailsByRelationIds(env, pageIds);
  return details.map((item) => item.title).filter(Boolean);
}

function parseStudyPayload(payload: Record<string, unknown>) {
  const studyMinutesPresent = Object.prototype.hasOwnProperty.call(payload, "study_minutes");
  const studySessionsPresent = Object.prototype.hasOwnProperty.call(payload, "study_sessions");
  const studyLastUsedAtPresent = Object.prototype.hasOwnProperty.call(payload, "study_last_used_at");
  const studyMinutes =
    typeof payload.study_minutes === "number" && Number.isFinite(payload.study_minutes)
      ? payload.study_minutes
      : null;
  const studySessions =
    typeof payload.study_sessions === "number" && Number.isFinite(payload.study_sessions)
      ? payload.study_sessions
      : null;
  const studyLastUsedAt =
    typeof payload.study_last_used_at === "string" ? payload.study_last_used_at.trim() : "";
  return { studyMinutesPresent, studySessionsPresent, studyLastUsedAtPresent, studyMinutes, studySessions, studyLastUsedAt };
}

function applyStudyUpdateProperties(
  updateProperties: Record<string, any>,
  dailyLogProperties: Record<string, any>,
  parsed: ReturnType<typeof parseStudyPayload>,
): void {
  if (parsed.studyMinutesPresent) {
    if (parsed.studyMinutes !== null && hasPropertyType(dailyLogProperties, "Study Minutes", "number")) {
      updateProperties["Study Minutes"] = createNumberProperty(parsed.studyMinutes);
    } else {
      console.warn("[generate_diary] study_update_skipped_reason=invalid_or_missing_property field=study_minutes");
    }
  }
  if (parsed.studySessionsPresent) {
    if (parsed.studySessions !== null && hasPropertyType(dailyLogProperties, "Study Sessions", "number")) {
      updateProperties["Study Sessions"] = createNumberProperty(parsed.studySessions);
    } else {
      console.warn("[generate_diary] study_update_skipped_reason=invalid_or_missing_property field=study_sessions");
    }
  }
  if (parsed.studyLastUsedAtPresent) {
    if (parsed.studyLastUsedAt && hasPropertyType(dailyLogProperties, "Study Last Used At", "date")) {
      updateProperties["Study Last Used At"] = createDateProperty(parsed.studyLastUsedAt);
    } else if (parsed.studyLastUsedAt) {
      console.warn("[generate_diary] study_update_skipped_reason=invalid_or_missing_property field=study_last_used_at");
    }
  }
}

async function handleDailyLogGenerateDiary(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/generate_diary");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }
  const targetDate = typeof payload.target_date === "string" ? payload.target_date.trim() : "";
  if (!targetDate || !isValidDateString(targetDate)) {
    return badRequest("invalid target_date format");
  }
  const diaryNotificationHash =
    typeof payload.diary_notification_hash === "string"
      ? payload.diary_notification_hash.trim()
      : "";
  const diaryNotificationSentAt =
    typeof payload.diary_notification_sent_at === "string"
      ? payload.diary_notification_sent_at.trim()
      : "";
  const diaryNotificationVersionRaw =
    typeof payload.diary_notification_version === "number"
      ? payload.diary_notification_version
      : null;
  const diary = typeof payload.diary === "string" ? payload.diary.trim() : "";
  const sleepAnalysisJp =
    typeof payload.sleep_analysis_jp === "string" ? payload.sleep_analysis_jp.trim() : "";
  const todayConditionForecastJp =
    typeof payload.today_condition_forecast_jp === "string"
      ? payload.today_condition_forecast_jp.trim()
      : "";
  const todayAdvice = typeof payload.today_advice === "string" ? payload.today_advice.trim() : "";
  const diaryInputHash =
    typeof payload.diary_input_hash === "string" ? payload.diary_input_hash.trim() : "";
  const todayAdviceInputHash =
    typeof payload.today_advice_input_hash === "string"
      ? payload.today_advice_input_hash.trim()
      : "";
  const diaryGeneratedAt =
    typeof payload.diary_generated_at === "string" ? payload.diary_generated_at.trim() : "";
  const todayAdviceGeneratedAt =
    typeof payload.today_advice_generated_at === "string"
      ? payload.today_advice_generated_at.trim()
      : "";
  const weatherText = typeof payload.weather === "string" ? payload.weather.trim() : "";
  const weatherSummaryText =
    typeof payload.weather_summary === "string" ? payload.weather_summary.trim() : "";
  const weatherLocation =
    typeof payload.weather_location === "string" ? payload.weather_location.trim() : "";
  const weatherTempMaxC =
    typeof payload.weather_temp_max_c === "number" ? payload.weather_temp_max_c : null;
  const weatherTempMinC =
    typeof payload.weather_temp_min_c === "number" ? payload.weather_temp_min_c : null;
  const weatherPrecipProbabilityMax =
    typeof payload.weather_precip_probability_max === "number"
      ? payload.weather_precip_probability_max
      : null;
  const weatherCode = typeof payload.weather_code === "number" ? payload.weather_code : null;
  const weatherRetrievedAt =
    typeof payload.weather_retrieved_at === "string" ? payload.weather_retrieved_at.trim() : "";
  const weatherInputHash =
    typeof payload.weather_input_hash === "string" ? payload.weather_input_hash.trim() : "";
  const weatherGeneratedAt =
    typeof payload.weather_generated_at === "string" ? payload.weather_generated_at.trim() : "";
  const mailInputHash =
    typeof payload.mail_input_hash === "string" ? payload.mail_input_hash.trim() : "";
  const mailInputSnapshot =
    typeof payload.mail_input_snapshot === "string" ? payload.mail_input_snapshot.trim() : "";
  const mailSentAt =
    typeof payload.mail_sent_at === "string" ? payload.mail_sent_at.trim() : "";
  const mailVersion =
    typeof payload.mail_version === "number" ? payload.mail_version : null;
  const studyPayload = parseStudyPayload(payload);
  const { studyMinutesPresent, studySessionsPresent, studyLastUsedAtPresent } = studyPayload;
  console.log(
    `[generate_diary] study_minutes_present=${studyMinutesPresent} study_sessions_present=${studySessionsPresent} study_last_used_at_present=${studyLastUsedAtPresent} study_minutes_value=${studyPayload.studyMinutes === null ? "null" : studyPayload.studyMinutes}`,
  );
  if (
    !diary &&
    !sleepAnalysisJp &&
    !todayConditionForecastJp &&
    !todayAdvice &&
    weatherText === "" &&
    weatherSummaryText === "" &&
    weatherLocation === "" &&
    weatherTempMaxC === null &&
    weatherTempMinC === null &&
    weatherPrecipProbabilityMax === null &&
    weatherCode === null &&
    !weatherRetrievedAt &&
    !weatherInputHash &&
    !weatherGeneratedAt &&
    !mailInputHash &&
    !mailInputSnapshot &&
    !mailSentAt &&
    mailVersion === null &&
    !studyMinutesPresent &&
    !studySessionsPresent &&
    !studyLastUsedAtPresent &&
    !diaryInputHash &&
    !todayAdviceInputHash &&
    !diaryGeneratedAt &&
    !todayAdviceGeneratedAt
  ) {
    return jsonResponse({
      ok: true,
      found: true,
      target_date: targetDate,
      updated: false,
      reason: "no_updatable_content",
    });
  }

  const queryResponse = await notionFetch(env, `/databases/${env.DAILY_LOG_DB_ID}/query`, {
    method: "POST",
    body: JSON.stringify({
      page_size: 1,
      filter: {
        property: "Target Date",
        date: { equals: targetDate },
      },
    }),
  });
  if (!queryResponse.ok) {
    return notionErrorResponse(queryResponse, "handleDailyLogGenerateDiary.query");
  }

  const queryData = await queryResponse.json();
  const page = (queryData.results ?? [])[0];
  if (!page?.id) {
    return jsonResponse({ ok: true, found: false, target_date: targetDate, updated: false, reason: "not_found" });
  }

  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  const updateProperties: Record<string, any> = {};
  if (diary) {
    updateProperties.Diary = createRichTextPropertyWithLimit(diary, DIARY_RICH_TEXT_LIMIT);
  }
  if (todayAdvice) {
    updateProperties["Today advice"] = createRichTextPropertyWithLimit(todayAdvice, DIARY_RICH_TEXT_LIMIT);
  }
  const weatherSummaryPropertyName = getWeatherSummaryPropertyName(env);
  const weatherSummaryTextResolved =
    weatherSummaryText ||
    weatherText ||
    buildWeatherSummaryText({
      weatherCode,
      weatherTempMaxC,
      weatherTempMinC,
      weatherPrecipProbabilityMax,
    });
  const weatherSelectLabel = inferWeatherSelectLabel(weatherCode, weatherSummaryTextResolved);
  const weatherPropertyName = getWeatherPropertyName(env);
  const weatherPropertyResolvedName = resolvePropertyName(
    dailyLogProperties,
    weatherPropertyName,
    "generate_diary:weather",
  );
  const weatherPropertyType = weatherPropertyResolvedName
    ? dailyLogProperties[weatherPropertyResolvedName]?.type
    : "missing";
  const weatherSummaryPropertyResolvedName = resolvePropertyName(
    dailyLogProperties,
    weatherSummaryPropertyName,
    "generate_diary:weather_summary",
  );
  const weatherSummaryPropertyType = weatherSummaryPropertyResolvedName
    ? dailyLogProperties[weatherSummaryPropertyResolvedName]?.type
    : "missing";
  console.log(
    `[generate_diary] weather_summary_generated=${weatherSummaryTextResolved !== ""} ` +
      `weather_summary_text=${JSON.stringify(weatherSummaryTextResolved)} ` +
      `weather_select_label=${weatherSelectLabel ?? "null"} ` +
      `weather_property_type=${weatherPropertyType} ` +
      `weather_summary_property_type=${weatherSummaryPropertyType}`,
  );
  if (weatherSummaryPropertyType === "rich_text") {
    updateProperties[weatherSummaryPropertyName] = createRichTextProperty(weatherSummaryTextResolved);
  }
  let weatherSummarySaved = weatherSummaryPropertyType === "rich_text";
  let weatherSelectSaved = false;
  let weatherSelectSkipReason = "";
  if (weatherPropertyType === "select") {
    if (weatherSelectLabel) {
      updateProperties[weatherPropertyName] = createSelectProperty(weatherSelectLabel);
      weatherSelectSaved = true;
    } else {
      weatherSelectSkipReason = "weather_select_label_unresolved";
    }
  } else if (weatherPropertyType === "rich_text") {
    if (weatherSelectLabel) {
      updateProperties[weatherPropertyName] = createRichTextProperty(weatherSelectLabel);
      weatherSelectSaved = true;
    } else {
      weatherSelectSkipReason = "weather_select_label_unresolved";
    }
  } else if (weatherPropertyType === "missing") {
    weatherSelectSkipReason = "weather_property_missing";
  } else {
    weatherSelectSkipReason = `unsupported_weather_property_type:${weatherPropertyType}`;
  }
  const weatherLocationPropertyName = getWeatherLocationPropertyName(env);
  if (weatherLocation && hasPropertyType(dailyLogProperties, weatherLocationPropertyName, "rich_text")) {
    updateProperties[weatherLocationPropertyName] = createRichTextProperty(weatherLocation);
  }
  const weatherTempMaxCPropertyName = getWeatherTempMaxCPropertyName(env);
  if (weatherTempMaxC !== null && hasPropertyType(dailyLogProperties, weatherTempMaxCPropertyName, "number")) {
    updateProperties[weatherTempMaxCPropertyName] = { number: weatherTempMaxC };
  }
  const weatherTempMinCPropertyName = getWeatherTempMinCPropertyName(env);
  if (weatherTempMinC !== null && hasPropertyType(dailyLogProperties, weatherTempMinCPropertyName, "number")) {
    updateProperties[weatherTempMinCPropertyName] = { number: weatherTempMinC };
  }
  const weatherPrecipProbabilityMaxPropertyName = getWeatherPrecipProbabilityMaxPropertyName(env);
  if (
    weatherPrecipProbabilityMax !== null &&
    hasPropertyType(dailyLogProperties, weatherPrecipProbabilityMaxPropertyName, "number")
  ) {
    updateProperties[weatherPrecipProbabilityMaxPropertyName] = { number: weatherPrecipProbabilityMax };
  }
  const weatherCodePropertyName = getWeatherCodePropertyName(env);
  if (weatherCode !== null && hasPropertyType(dailyLogProperties, weatherCodePropertyName, "number")) {
    updateProperties[weatherCodePropertyName] = { number: weatherCode };
  }
  const weatherRetrievedAtPropertyName = getWeatherRetrievedAtPropertyName(env);
  if (weatherRetrievedAt) {
    if (hasPropertyType(dailyLogProperties, weatherRetrievedAtPropertyName, "date")) {
      updateProperties[weatherRetrievedAtPropertyName] = createDateProperty(weatherRetrievedAt);
    } else if (hasPropertyType(dailyLogProperties, weatherRetrievedAtPropertyName, "rich_text")) {
      updateProperties[weatherRetrievedAtPropertyName] = createRichTextProperty(weatherRetrievedAt);
    }
  }
  const weatherInputHashPropertyName = getWeatherInputHashPropertyName(env);
  if (weatherInputHash && hasPropertyType(dailyLogProperties, weatherInputHashPropertyName, "rich_text")) {
    updateProperties[weatherInputHashPropertyName] = createRichTextProperty(weatherInputHash);
  }
  const weatherGeneratedAtPropertyName = getWeatherGeneratedAtPropertyName(env);
  if (weatherGeneratedAt) {
    if (hasPropertyType(dailyLogProperties, weatherGeneratedAtPropertyName, "date")) {
      updateProperties[weatherGeneratedAtPropertyName] = createDateProperty(weatherGeneratedAt);
    } else if (hasPropertyType(dailyLogProperties, weatherGeneratedAtPropertyName, "rich_text")) {
      updateProperties[weatherGeneratedAtPropertyName] = createRichTextProperty(weatherGeneratedAt);
    }
  }
  if (mailInputHash && hasPropertyType(dailyLogProperties, MAIL_INPUT_HASH_PROPERTY_NAME, "rich_text")) {
    updateProperties[MAIL_INPUT_HASH_PROPERTY_NAME] = createRichTextPropertyWithLimit(
      mailInputHash,
      NOTES_RICH_TEXT_LIMIT,
    );
  }
  if (mailInputSnapshot && hasPropertyType(dailyLogProperties, MAIL_INPUT_SNAPSHOT_PROPERTY_NAME, "rich_text")) {
    updateProperties[MAIL_INPUT_SNAPSHOT_PROPERTY_NAME] = createRichTextPropertyWithLimit(
      mailInputSnapshot,
      NOTES_RICH_TEXT_LIMIT,
    );
  }
  if (mailSentAt && hasPropertyType(dailyLogProperties, MAIL_SENT_AT_PROPERTY_NAME, "date")) {
    updateProperties[MAIL_SENT_AT_PROPERTY_NAME] = createDateProperty(mailSentAt);
  }
  if (mailVersion !== null && hasPropertyType(dailyLogProperties, MAIL_VERSION_PROPERTY_NAME, "number")) {
    updateProperties[MAIL_VERSION_PROPERTY_NAME] = createNumberProperty(mailVersion);
  }
  applyStudyUpdateProperties(updateProperties, dailyLogProperties, studyPayload);
  const diaryInputHashPropertyName = getDiaryInputHashPropertyName(env);
  if (diaryInputHash && hasPropertyType(dailyLogProperties, diaryInputHashPropertyName, "rich_text")) {
    updateProperties[diaryInputHashPropertyName] = createRichTextProperty(diaryInputHash);
  }
  const todayAdviceInputHashPropertyName = getTodayAdviceInputHashPropertyName(env);
  if (
    todayAdviceInputHash &&
    hasPropertyType(dailyLogProperties, todayAdviceInputHashPropertyName, "rich_text")
  ) {
    updateProperties[todayAdviceInputHashPropertyName] = createRichTextProperty(todayAdviceInputHash);
  }
  const dailyLogHealthPropertyNames = getDailyLogHealthPropertyNames(env);
  const sleepAnalysisPropertyName = canUseProperty(
    dailyLogProperties,
    dailyLogHealthPropertyNames.sleepAnalysisJp,
    "rich_text",
    "generate_diary:sleep_analysis",
    getSleepPropertyAliases(SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp),
  );
  if (sleepAnalysisJp && sleepAnalysisPropertyName) {
    console.log(`Saving Sleep Analysis JP for ${targetDate} via ${sleepAnalysisPropertyName}`);
    updateProperties[sleepAnalysisPropertyName] = createRichTextPropertyWithLimit(
      sleepAnalysisJp,
      DIARY_RICH_TEXT_LIMIT,
    );
  }
  const todayConditionForecastPropertyName = canUseProperty(
    dailyLogProperties,
    dailyLogHealthPropertyNames.todayConditionForecastJp,
    "rich_text",
    "generate_diary:today_condition_forecast",
    getSleepPropertyAliases(SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp),
  );
  if (todayConditionForecastJp && todayConditionForecastPropertyName) {
    console.log(`Saving Today Condition Forecast JP for ${targetDate} via ${todayConditionForecastPropertyName}`);
    updateProperties[todayConditionForecastPropertyName] = createRichTextPropertyWithLimit(
      todayConditionForecastJp,
      DIARY_RICH_TEXT_LIMIT,
    );
  }

  const diaryGeneratedAtPropertyName = getDiaryGeneratedAtPropertyName(env);
  if (diaryGeneratedAt) {
    if (hasPropertyType(dailyLogProperties, diaryGeneratedAtPropertyName, "date")) {
      updateProperties[diaryGeneratedAtPropertyName] = createDateProperty(diaryGeneratedAt);
    } else if (hasPropertyType(dailyLogProperties, diaryGeneratedAtPropertyName, "rich_text")) {
      updateProperties[diaryGeneratedAtPropertyName] = createRichTextProperty(diaryGeneratedAt);
    }
  } else if (diary && hasPropertyType(dailyLogProperties, diaryGeneratedAtPropertyName, "date")) {
    updateProperties[diaryGeneratedAtPropertyName] = createDateProperty(getJstDateString());
  } else if (diary && hasPropertyType(dailyLogProperties, diaryGeneratedAtPropertyName, "rich_text")) {
    updateProperties[diaryGeneratedAtPropertyName] = createRichTextProperty(
      formatJstDateTime(getJstDateString()),
    );
  }

  const todayAdviceGeneratedAtPropertyName = getTodayAdviceGeneratedAtPropertyName(env);
  if (todayAdviceGeneratedAt) {
    if (hasPropertyType(dailyLogProperties, todayAdviceGeneratedAtPropertyName, "date")) {
      updateProperties[todayAdviceGeneratedAtPropertyName] = createDateProperty(todayAdviceGeneratedAt);
    } else if (hasPropertyType(dailyLogProperties, todayAdviceGeneratedAtPropertyName, "rich_text")) {
      updateProperties[todayAdviceGeneratedAtPropertyName] = createRichTextProperty(todayAdviceGeneratedAt);
    }
  } else if (
    todayAdvice &&
    hasPropertyType(dailyLogProperties, todayAdviceGeneratedAtPropertyName, "date")
  ) {
    updateProperties[todayAdviceGeneratedAtPropertyName] = createDateProperty(getJstDateString());
  } else if (
    todayAdvice &&
    hasPropertyType(dailyLogProperties, todayAdviceGeneratedAtPropertyName, "rich_text")
  ) {
    updateProperties[todayAdviceGeneratedAtPropertyName] = createRichTextProperty(
      formatJstDateTime(getJstDateString()),
    );
  }

  if (!Object.keys(updateProperties).length) {
    console.warn(
      `generate_diary property_unavailable target_date=${targetDate} requested_fields=${JSON.stringify({
        diary: Boolean(diary),
        today_advice: Boolean(todayAdvice),
        weather: weatherText !== "",
        weather_summary: weatherSummaryText !== "",
        weather_location: Boolean(weatherLocation),
        weather_temp_max_c: weatherTempMaxC !== null,
        weather_temp_min_c: weatherTempMinC !== null,
        weather_precip_probability_max: weatherPrecipProbabilityMax !== null,
        weather_code: weatherCode !== null,
        weather_retrieved_at: Boolean(weatherRetrievedAt),
        weather_input_hash: Boolean(weatherInputHash),
        weather_generated_at: Boolean(weatherGeneratedAt),
      })}`,
    );
    return jsonResponse({
      ok: true,
      found: true,
      target_date: targetDate,
      page_id: page.id,
      updated: false,
      reason: "property_unavailable",
    });
  }
  const updateResponse = await notionFetch(env, `/pages/${page.id}`, {
    method: "PATCH",
    body: JSON.stringify({ properties: updateProperties }),
  });
  if (!updateResponse.ok) {
    if (weatherPropertyType === "select" && updateProperties[weatherPropertyName]) {
      const fallbackProperties = { ...updateProperties };
      delete fallbackProperties[weatherPropertyName];
      const selectErrorDetails = await getNotionErrorDetails(updateResponse);
      weatherSelectSaved = false;
      weatherSelectSkipReason = "select_option_not_found";
      console.warn(
        `[generate_diary] weather select update failed; retry without Weather. ` +
          `skip_reason=${weatherSelectSkipReason} status=${selectErrorDetails.status} ` +
          `message=${selectErrorDetails.message}`,
      );
      const retryResponse = await notionFetch(env, `/pages/${page.id}`, {
        method: "PATCH",
        body: JSON.stringify({ properties: fallbackProperties }),
      });
      if (!retryResponse.ok) {
        return notionErrorResponse(retryResponse, "handleDailyLogGenerateDiary.update_weather_summary_retry");
      }
      weatherSummarySaved = weatherSummaryPropertyType === "rich_text";
      console.log(
        `[generate_diary] weather_summary_saved=${weatherSummarySaved} weather_select_saved=${weatherSelectSaved} ` +
          `weather_select_skip_reason=${weatherSelectSkipReason}`,
      );
      return jsonResponse({
        ok: true,
        found: true,
        target_date: targetDate,
        page_id: page.id,
        updated: true,
        reason: "updated_with_weather_select_skip",
        weather_summary_saved: weatherSummarySaved,
        weather_select_saved: weatherSelectSaved,
        weather_select_skip_reason: weatherSelectSkipReason,
      });
    }
    return notionErrorResponse(updateResponse, "handleDailyLogGenerateDiary.update");
  }
  console.log(
    `[generate_diary] weather_summary_saved=${weatherSummarySaved} weather_select_saved=${weatherSelectSaved} ` +
      `weather_select_skip_reason=${weatherSelectSkipReason || "-"}`,
  );

  return jsonResponse({
    ok: true,
    found: true,
    target_date: targetDate,
    page_id: page.id,
    updated: true,
    reason: "updated",
    weather_summary_saved: weatherSummarySaved,
    weather_select_saved: weatherSelectSaved,
    weather_select_skip_reason: weatherSelectSkipReason || null,
  });
}

async function handleDailyLogMarkDiaryNotified(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/mark_diary_notified");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }
  const targetDate = typeof payload.target_date === "string" ? payload.target_date.trim() : "";
  if (!targetDate || !isValidDateString(targetDate)) {
    return badRequest("invalid target_date format");
  }
  const diaryNotificationHash =
    typeof payload.diary_notification_hash === "string"
      ? payload.diary_notification_hash.trim()
      : "";
  const diaryNotificationSentAt =
    typeof payload.diary_notification_sent_at === "string"
      ? payload.diary_notification_sent_at.trim()
      : "";
  const diaryNotificationVersionRaw =
    typeof payload.diary_notification_version === "number"
      ? payload.diary_notification_version
      : null;

  const queryResponse = await notionFetch(env, `/databases/${env.DAILY_LOG_DB_ID}/query`, {
    method: "POST",
    body: JSON.stringify({
      page_size: 1,
      filter: {
        property: "Target Date",
        date: { equals: targetDate },
      },
    }),
  });

  if (!queryResponse.ok) {
    return notionErrorResponse(queryResponse, "handleDailyLogMarkDiaryNotified.query");
  }

  const queryData = await queryResponse.json();
  const page = (queryData.results ?? [])[0];
  if (!page?.id) {
    return new Response(
      JSON.stringify({ ok: true, found: false, target_date: targetDate, updated: false }),
      { headers: jsonHeaders },
    );
  }

  const notificationSentPropertyName = getDiaryNotificationSentPropertyName(env);
  const notificationHashPropertyName = getDiaryNotificationHashPropertyName(env);
  const notificationSentAtPropertyName = getDiaryNotificationSentAtPropertyName(env);
  const notificationVersionPropertyName = getDiaryNotificationVersionPropertyName(env);
  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  if (!hasPropertyType(dailyLogProperties, notificationSentPropertyName, "checkbox")) {
    console.warn(
      `Diary notification mark skipped: property "${notificationSentPropertyName}" missing or invalid type.`,
    );
    return new Response(
      JSON.stringify({
        ok: true,
        found: true,
        target_date: targetDate,
        page_id: page.id,
        updated: false,
        reason: "property_unavailable",
      }),
      { headers: jsonHeaders },
    );
  }

  const updateProperties: Record<string, unknown> = {
    [notificationSentPropertyName]: createCheckboxProperty(true),
  };
  if (
    diaryNotificationHash &&
    hasPropertyType(dailyLogProperties, notificationHashPropertyName, "rich_text")
  ) {
    updateProperties[notificationHashPropertyName] = createRichTextProperty(
      diaryNotificationHash,
    );
  }
  if (
    diaryNotificationSentAt &&
    hasPropertyType(dailyLogProperties, notificationSentAtPropertyName, "date")
  ) {
    updateProperties[notificationSentAtPropertyName] = createDateProperty(
      diaryNotificationSentAt,
    );
  }
  if (
    diaryNotificationVersionRaw !== null &&
    hasPropertyType(dailyLogProperties, notificationVersionPropertyName, "number")
  ) {
    updateProperties[notificationVersionPropertyName] = createNumberProperty(
      Math.trunc(diaryNotificationVersionRaw),
    );
  }

  const updateResponse = await notionFetch(env, `/pages/${page.id}`, {
    method: "PATCH",
    body: JSON.stringify({
      properties: updateProperties,
    }),
  });
  if (!updateResponse.ok) {
    return notionErrorResponse(updateResponse, "handleDailyLogMarkDiaryNotified.update");
  }

  return new Response(
    JSON.stringify({
      ok: true,
      found: true,
      target_date: targetDate,
      page_id: page.id,
      updated: true,
    }),
    { headers: jsonHeaders },
  );
}

async function handleMoodNotesIngest(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /ingest/mood-notes");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  await validateDatabaseSchema(
    env,
    env.DAILY_LOG_DB_ID,
    buildDailyLogMoodNotesProperties(),
    { Mood: [...MOOD_OPTIONS] },
  );

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const { data, error } = validateMoodNotesPayload(payload);
  if (error) {
    return error;
  }
  if (!data) {
    return badRequest("invalid payload");
  }

  return updateMoodNotes(env, data);
}

async function handleMoodNotesConfirm(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return methodNotAllowed();
  }
  const url = new URL(request.url);
  const targetDate = url.searchParams.get("date")?.trim() ?? "";
  const token = url.searchParams.get("token")?.trim() ?? "";
  if (!targetDate || !token) {
    return badRequest("missing date or token");
  }
  if (!isValidDateString(targetDate)) {
    return badRequest("invalid date format");
  }
  try {
    const payload = await verifyMailLinkToken(token, env);
    if (payload.date !== targetDate) {
      return createTextResponse("date mismatch", 403);
    }
  } catch (error) {
    if (error instanceof MailLinkTokenError) {
      return createTextResponse(error.message, error.status);
    }
    throw error;
  }

  return buildMoodNotesConfirmHtml(targetDate, token);
}

async function handleMoodNotesExecute(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed();
  }

  await validateDatabaseSchema(
    env,
    env.DAILY_LOG_DB_ID,
    buildDailyLogMoodNotesProperties(),
    { Mood: [...MOOD_OPTIONS] },
  );

  const payload = await parseRequestBody(request);
  const targetDate = payload.date?.trim() ?? "";
  const token = payload.token?.trim() ?? "";
  if (!targetDate || !token) {
    return badRequest("missing date or token");
  }
  if (!isValidDateString(targetDate)) {
    return badRequest("invalid date format");
  }

  let tokenPayload: MailLinkPayload;
  try {
    tokenPayload = await verifyMailLinkToken(token, env);
  } catch (error) {
    if (error instanceof MailLinkTokenError) {
      return createTextResponse(error.message, error.status);
    }
    throw error;
  }

  if (tokenPayload.date !== targetDate) {
    return createTextResponse("date mismatch", 403);
  }

  const rawMood = payload.mood?.trim() ?? "";
  const mood = rawMood ? normalizeMoodInput(rawMood) : undefined;
  if (rawMood && !mood) {
    return badRequest("invalid mood");
  }
  const notes = payload.notes !== undefined ? String(payload.notes) : undefined;
  const modeRaw = payload.mode?.trim().toLowerCase() ?? "append";
  const mode = modeRaw === "replace" ? "replace" : "append";

  const notesValue = notes?.trim() ?? "";
  if (!mood && !notesValue) {
    const message = "どちらか入力してください";
    if (request.headers.get("content-type")?.includes("application/json")) {
      return badRequest(message);
    }
    return new Response(
      `<!doctype html><html><head><meta charset="utf-8" /><title>入力が必要です</title></head><body><p>${message}</p><p><a href="/confirm/mood-notes?date=${targetDate}&token=${token}">戻る</a></p></body></html>`,
      { status: 400, headers: { "content-type": "text/html; charset=utf-8" } },
    );
  }

  console.log("Mood/Notes execute", {
    targetDate,
    mood: mood ?? null,
    notes: notesValue ? "yes" : "no",
  });

  const result = await updateMoodNotes(env, {
    targetDate,
    mood,
    notes,
    mode,
  });

  if (request.headers.get("content-type")?.includes("application/json")) {
    return result;
  }

  if (result.ok) {
    return createHtmlPage(
      "Mood / Notes Updated",
      `<p>OK (${targetDate})</p><p><a href="/confirm/mood-notes?date=${targetDate}&token=${token}">戻る</a></p>`,
    );
  }

  return result;
}

async function handleDailyLogEnsure(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/ensure");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  try {
    console.log(
    `Daily Log schema validation uses sleep property names: ${[
      SLEEP_PROPERTY_MAPPINGS.sleepStart.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepEnd.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepScore.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepSource.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.displayName,
      SLEEP_PROPERTY_MAPPINGS.deepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.remDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessStars.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp.displayName,
      SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp.displayName,
    ].join(", ")}`
  );
  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));
  } catch (error) {
    if (isOptionalLocationSummaryValidationError(error)) {
      console.info("Location summary is optional; skip validation");
    } else {
      throw error;
    }
  }

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const { data, error } = validateDailyLogEnsurePayload(payload);
  if (error) {
    return error;
  }
  if (!data) {
    return badRequest("invalid payload");
  }

  const { targetDate, title, source, mailId } = data;

  const resolvedDailyLog = await resolveDailyLogPageForDate(env, targetDate);
  const existingPage = resolvedDailyLog.canonicalPage;
  if (existingPage) {
    return new Response(JSON.stringify({ ok: true, page_id: existingPage.id, duplicate_info: buildDuplicateInfo(resolvedDailyLog, existingPage.id) }), {
      headers: jsonHeaders,
    });
  }

  const properties: Record<string, any> = {
    [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
    "Target Date": createDateProperty(targetDate),
    Date: createDateProperty(targetDate),
    "Activity Summary": createRichTextProperty(""),
    Diary: createRichTextProperty(""),
    "Mail ID": createRichTextProperty(mailId),
    Source: createSelectProperty(source),
  };
  logDailyLogPatchKeys({
    endpointName: "/execute/api/daily_log/ensure",
    targetDate,
    pageId: null,
    canonicalPageId: null,
    properties,
    reason: "ensure_create",
  });

  const resultResponse = await notionFetch(env, "/pages", {
    method: "POST",
    body: JSON.stringify({
      parent: { database_id: env.DAILY_LOG_DB_ID },
      properties,
    }),
  });

  if (!resultResponse.ok) {
    return notionErrorResponse(resultResponse, "handleDailyLogEnsure.create");
  }

  const pageId = (await resultResponse.json()).id;
  return new Response(JSON.stringify({ ok: true, page_id: pageId, duplicate_info: { detected: false, duplicate_count: 0, canonical_page_id: pageId, duplicate_page_ids: [], merged_fields: [], merge_completed: true, duplicate_fields_present: { location_summary: false, meal_photos: false, mood: false, notes: false } } }), {
    headers: jsonHeaders,
  });
}

function isOptionalLocationSummaryValidationError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }

  const message = error.message;
  if (!message.includes("Database schema validation failed")) {
    return false;
  }

  const missingMatch = message.match(/Missing: ([^;]+)/);
  if (!missingMatch) {
    return false;
  }

  const missingProperties = missingMatch[1]
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);

  return missingProperties.length === 1 && missingProperties[0] === "Location summary (GPT)";
}


async function queryDailyLogCandidatesForDate(env: Env, targetDate: string): Promise<any[]> {
  const [byDate, byTargetDate, byTitle] = await Promise.all([
    queryDatabaseAll(env, env.DAILY_LOG_DB_ID, { property: "Date", date: { equals: targetDate } }),
    queryDatabaseAll(env, env.DAILY_LOG_DB_ID, { property: "Target Date", date: { equals: targetDate } }),
    queryDatabaseAll(env, env.DAILY_LOG_DB_ID, { property: TITLE_PROPERTIES.dailyLog, title: { contains: targetDate } }),
  ]);
  const byId = new Map<string, any>();
  for (const page of [...byDate, ...byTargetDate, ...byTitle] as any[]) {
    if (isPageMatchedByDateOrTitle(page as any, targetDate)) byId.set(page.id, page);
  }
  return Array.from(byId.values());
}

async function resolveDailyLogPageForDate(env: Env, targetDate: string): Promise<{ canonicalPage: any | null; duplicatePages: any[]; mergedFields: string[]; mergeCompleted: boolean; duplicateFieldsPresent: Record<string, boolean> }> {
  const candidates = await queryDailyLogCandidatesForDate(env, targetDate);
  const canonicalPage = chooseCanonicalDailyLogPage(candidates, targetDate);
  if (!canonicalPage) return { canonicalPage: null, duplicatePages: [], mergedFields: [], mergeCompleted: false, duplicateFieldsPresent: { location_summary: false, meal_photos: false, mood: false, notes: false } };
  const duplicatePages = candidates.filter((p) => p.id !== canonicalPage.id);
  if (!duplicatePages.length) return { canonicalPage, duplicatePages, mergedFields: [], mergeCompleted: true, duplicateFieldsPresent: { location_summary: false, meal_photos: false, mood: false, notes: false } };
  const patch = buildDuplicateMergePatch(canonicalPage, duplicatePages);
  if (!patch.hasChanges) {
    return { canonicalPage, duplicatePages, mergedFields: [], mergeCompleted: true, duplicateFieldsPresent: patch.duplicateFieldsPresent };
  }
  const updateResponse = await notionFetch(env, `/pages/${canonicalPage.id}`, { method: "PATCH", body: JSON.stringify({ properties: patch.properties }) });
  if (!updateResponse.ok) {
    console.warn(`[daily_log.resolver] duplicate merge failed target_date=${targetDate} canonical_page_id=${canonicalPage.id}`);
    return { canonicalPage, duplicatePages, mergedFields: patch.mergedFields, mergeCompleted: false, duplicateFieldsPresent: patch.duplicateFieldsPresent };
  }
  const refreshed = await notionFetch(env, `/pages/${canonicalPage.id}`);
  const refreshedPage = refreshed.ok ? await refreshed.json() : canonicalPage;
  return { canonicalPage: refreshedPage, duplicatePages, mergedFields: patch.mergedFields, mergeCompleted: true, duplicateFieldsPresent: patch.duplicateFieldsPresent };
}


function buildDuplicateInfo(resolved: { duplicatePages: any[]; mergedFields: string[]; mergeCompleted: boolean; duplicateFieldsPresent: Record<string, boolean> }, canonicalPageId: string) {
  return {
    detected: resolved.duplicatePages.length > 0,
    duplicate_count: resolved.duplicatePages.length,
    canonical_page_id: canonicalPageId,
    duplicate_page_ids: resolved.duplicatePages.map((item: any) => item.id),
    merged_fields: resolved.mergedFields,
    merge_completed: resolved.mergeCompleted,
    duplicate_fields_present: resolved.duplicateFieldsPresent,
  };
}

async function handleDailyLogRead(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return methodNotAllowed();
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  console.log(
    `Daily Log schema validation uses sleep property names: ${[
      SLEEP_PROPERTY_MAPPINGS.sleepStart.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepEnd.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepScore.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepSource.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepHeartRate.displayName,
      SLEEP_PROPERTY_MAPPINGS.deepDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.remDurationMin.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessStars.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.readinessBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineHrv.displayName,
      SLEEP_PROPERTY_MAPPINGS.baselineWakingBpm.displayName,
      SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp.displayName,
      SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp.displayName,
    ].join(", ")}`
  );
  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));

  const url = new URL(request.url);
  const targetDate = url.searchParams.get("date")?.trim() ?? "";
  if (!targetDate) {
    return badRequest("missing date");
  }
  if (!isValidDateString(targetDate)) {
    return badRequest("invalid date format");
  }

  const resolved = await resolveDailyLogPageForDate(env, targetDate);
  const page = resolved.canonicalPage;
  const duplicatePages = resolved.duplicatePages;
  if (!page) {
    return new Response(JSON.stringify({ found: false, target_date: targetDate }), {
      headers: jsonHeaders,
    });
  }

  const properties = page.properties ?? {};
  const summaryText = getPlainTextFromRichText(properties["Activity Summary"]);
  const summaryHtml = getPlainTextFromRichText(properties.Diary);
  const diary = getPlainTextFromRichText(properties.Diary) || null;
  const date = getDateStartFromProperty(properties.Date) || null;
  const targetDateValue = getDateStartFromProperty(properties["Target Date"]) || targetDate;
  const place = getPlainTextFromRichText(properties.Place) || null;
  const doneTaskIds = getRelationIdsFromProperty(properties["Done Tasks"]);
  const dropTaskIds = getRelationIdsFromProperty(properties["Drop Tasks"]);
  const doneTaskDetails = await readTaskDetailsByRelationIds(env, doneTaskIds);
  const doneTaskTitles = doneTaskDetails.map((item) => item.title);
  const dropTaskTitles = await readTaskTitlesByRelationIds(env, dropTaskIds);
  const doneCount =
    typeof properties["Done Count"]?.number === "number"
      ? properties["Done Count"].number
      : doneTaskIds.length;
  const dropCount =
    typeof properties["Drop Count"]?.number === "number"
      ? properties["Drop Count"].number
      : dropTaskIds.length;
  const healthPropertyNames = getDailyLogHealthPropertyNames(env);
  const kcal = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.kcal, "daily_log_read:kcal"));
  const protein = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.protein, "daily_log_read:protein"));
  const fat = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.fat, "daily_log_read:fat"));
  const carb = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.carb, "daily_log_read:carb"));
  const sleepStart = getDateTimeFromProperty(getResolvedProperty(properties, healthPropertyNames.sleepStart, "daily_log_read:sleep_start"));
  const sleepEnd = getDateTimeFromProperty(getResolvedProperty(properties, healthPropertyNames.sleepEnd, "daily_log_read:sleep_end"));
  const sleepDurationMin = getNumberFromProperty(
    getResolvedProperty(properties, healthPropertyNames.sleepDurationMin, "daily_log_read:sleep_duration"),
  );
  const sleepScore = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.sleepScore, "daily_log_read:sleep_score"));
  const sleepSource = getStringFromProperty(getResolvedProperty(properties, healthPropertyNames.sleepSource, "daily_log_read:sleep_source"));
  const readinessStars = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.readinessStars, "daily_log_read:readiness_stars"));
  const readinessHrv = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.readinessHrv, "daily_log_read:readiness_hrv"));
  const readinessBpm = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.readinessBpm, "daily_log_read:readiness_bpm"));
  const baselineHrv = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.baselineHrv, "daily_log_read:baseline_hrv"));
  const baselineWakingBpm = getNumberFromProperty(
    getResolvedProperty(properties, healthPropertyNames.baselineWakingBpm, "daily_log_read:baseline_waking_bpm"),
  );
  const sleepHeartRate = getNumberFromProperty(
    getResolvedProperty(properties, healthPropertyNames.sleepHeartRate, "daily_log_read:sleep_heart_rate"),
  );
  const deepDurationMin = getNumberFromProperty(
    getResolvedProperty(properties, healthPropertyNames.deepDurationMin, "daily_log_read:deep_duration"),
  );
  const remDurationMin = getNumberFromProperty(getResolvedProperty(properties, healthPropertyNames.remDurationMin, "daily_log_read:rem_duration"));
  const sleepAnalysisJp =
    getPlainTextFromRichText(
      getResolvedProperty(
        properties,
        healthPropertyNames.sleepAnalysisJp,
        "daily_log_read:sleep_analysis",
        getSleepPropertyAliases(SLEEP_PROPERTY_MAPPINGS.sleepAnalysisJp),
      ),
    ) || null;
  const todayConditionForecastJp =
    getPlainTextFromRichText(
      getResolvedProperty(
        properties,
        healthPropertyNames.todayConditionForecastJp,
        "daily_log_read:today_condition_forecast",
        getSleepPropertyAliases(SLEEP_PROPERTY_MAPPINGS.todayConditionForecastJp),
      ),
    ) || null;
  const todayAdvice = getPlainTextFromRichText(properties["Today advice"]) || null;
  const studyMinutesPropertyName = resolvePropertyName(
    properties,
    "Study Minutes",
    "daily_log_read:study_minutes",
    ["study_minutes"],
  );
  const studySessionsPropertyName = resolvePropertyName(
    properties,
    "Study Sessions",
    "daily_log_read:study_sessions",
    ["study_sessions"],
  );
  const studyLastUsedAtPropertyName = resolvePropertyName(
    properties,
    "Study Last Used At",
    "daily_log_read:study_last_used_at",
    ["study_last_used_at"],
  );
  const studyMinutes = studyMinutesPropertyName
    ? getNumberFromProperty(properties[studyMinutesPropertyName])
    : null;
  const studySessions = studySessionsPropertyName
    ? getNumberFromProperty(properties[studySessionsPropertyName])
    : null;
  const studyLastUsedAt = studyLastUsedAtPropertyName
    ? getDateTimeFromProperty(properties[studyLastUsedAtPropertyName]) ||
      getStringFromProperty(properties[studyLastUsedAtPropertyName]) ||
      null
    : null;
  console.log(
    `[daily_log_read] study_minutes_present=${studyMinutes !== null} study_sessions_present=${studySessions !== null} study_last_used_at_present=${Boolean(studyLastUsedAt)} study_minutes_value=${studyMinutes === null ? "null" : studyMinutes}`,
  );
  const weatherPropertyName = resolveExactPropertyName(
    properties,
    getWeatherPropertyName(env),
    "daily_log_read:weather",
  );
  const weatherSummaryPropertyName = resolveExactPropertyName(
    properties,
    getWeatherSummaryPropertyName(env),
    "daily_log_read:weather_summary",
  );
  const weatherLocationPropertyName = resolveExactPropertyName(
    properties,
    getWeatherLocationPropertyName(env),
    "daily_log_read:weather_location",
  );
  const weatherTempMaxCPropertyName = resolveExactPropertyName(
    properties,
    getWeatherTempMaxCPropertyName(env),
    "daily_log_read:weather_temp_max_c",
  );
  const weatherTempMinCPropertyName = resolveExactPropertyName(
    properties,
    getWeatherTempMinCPropertyName(env),
    "daily_log_read:weather_temp_min_c",
  );
  const weatherPrecipProbabilityMaxPropertyName = resolveExactPropertyName(
    properties,
    getWeatherPrecipProbabilityMaxPropertyName(env),
    "daily_log_read:weather_precip_probability_max",
  );
  const weatherCodePropertyName = resolveExactPropertyName(
    properties,
    getWeatherCodePropertyName(env),
    "daily_log_read:weather_code",
  );
  const weatherRetrievedAtPropertyName = resolveExactPropertyName(
    properties,
    getWeatherRetrievedAtPropertyName(env),
    "daily_log_read:weather_retrieved_at",
  );
  const weatherInputHashPropertyName = resolveExactPropertyName(
    properties,
    getWeatherInputHashPropertyName(env),
    "daily_log_read:weather_input_hash",
  );
  const weatherGeneratedAtPropertyName = resolveExactPropertyName(
    properties,
    getWeatherGeneratedAtPropertyName(env),
    "daily_log_read:weather_generated_at",
  );
  const weatherResolvedText = weatherPropertyName
    ? getStringFromProperty(properties[weatherPropertyName])
    : null;
  const weatherSummaryResolvedText = weatherSummaryPropertyName
    ? getStringFromProperty(properties[weatherSummaryPropertyName])
    : null;
  const weatherSummary = (weatherSummaryResolvedText || weatherResolvedText || null);
  const weatherLegacyText = (weatherResolvedText || weatherSummaryResolvedText || null);
  const weatherLocation =
    (weatherLocationPropertyName
      ? getPlainTextFromRichText(properties[weatherLocationPropertyName])
      : null) || null;
  const weatherTempMaxC =
    (weatherTempMaxCPropertyName ? getNumberFromProperty(properties[weatherTempMaxCPropertyName]) : null) || null;
  const weatherTempMinC =
    (weatherTempMinCPropertyName ? getNumberFromProperty(properties[weatherTempMinCPropertyName]) : null) || null;
  const weatherPrecipProbabilityMax =
    (weatherPrecipProbabilityMaxPropertyName
      ? getNumberFromProperty(properties[weatherPrecipProbabilityMaxPropertyName])
      : null) || null;
  const weatherCode =
    (weatherCodePropertyName ? getNumberFromProperty(properties[weatherCodePropertyName]) : null) || null;
  const weatherRetrievedAt =
    (weatherRetrievedAtPropertyName
      ? getIsoStringFromProperty(properties[weatherRetrievedAtPropertyName])
      : null) || null;
  const weatherInputHash =
    (weatherInputHashPropertyName
      ? getPlainTextFromRichText(properties[weatherInputHashPropertyName])
      : null) || null;
  const weatherGeneratedAt =
    (weatherGeneratedAtPropertyName
      ? getIsoStringFromProperty(properties[weatherGeneratedAtPropertyName])
      : null) || null;
  const diaryInputHash =
    getPlainTextFromRichText(
      properties[getDiaryInputHashPropertyName(env)],
    ) || null;
  const todayAdviceInputHash =
    getPlainTextFromRichText(
      properties[getTodayAdviceInputHashPropertyName(env)],
    ) || null;
  const diaryGeneratedAt =
    getDateTimeFromProperty(properties[getDiaryGeneratedAtPropertyName(env)]) ||
    getStringFromProperty(properties[getDiaryGeneratedAtPropertyName(env)]) ||
    null;
  const todayAdviceGeneratedAt =
    getDateTimeFromProperty(properties[getTodayAdviceGeneratedAtPropertyName(env)]) ||
    getStringFromProperty(properties[getTodayAdviceGeneratedAtPropertyName(env)]) ||
    null;
  const mealSummary = getPlainTextFromRichText(properties["Meal summary"]) || null;
  const mealPhotosRaw = normalizeFilesFromProperty(properties["Meal Photos"]);
  const mealPhotos = getFileUrlsFromProperty(properties["Meal Photos"]);
  const activitySummary = getPlainTextFromRichText(properties["Activity Summary"]) || null;
  const dailyLogExpensesPropertyNames = getDailyLogExpensesPropertyNames(env);
  const expensesTotalRaw =
    typeof properties[dailyLogExpensesPropertyNames.total]?.number === "number"
      ? properties[dailyLogExpensesPropertyNames.total].number
      : null;
  const locationSummaryFields = resolveLocationSummaryFields(properties, env);
  const mood = properties.Mood?.select?.name ?? null;
  const notesPropertyName = getDailyLogNotesPropertyName(env);
  const notes = getPlainTextFromRichText(properties[notesPropertyName]) || null;
  const weight =
    typeof properties.Weight?.number === "number" ? properties.Weight.number : null;
  const pageUrl =
    typeof page.url === "string" && page.url ? page.url : buildNotionPageUrl(page.id);
  const diaryNotificationSentPropertyName = getDiaryNotificationSentPropertyName(env);
  const diaryNotificationHashPropertyName = getDiaryNotificationHashPropertyName(env);
  const diaryNotificationSentAtPropertyName = getDiaryNotificationSentAtPropertyName(env);
  const diaryNotificationVersionPropertyName = getDiaryNotificationVersionPropertyName(env);
  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  let diaryNotificationSent: boolean | null = null;
  let diaryNotificationHash: string | null = null;
  let diaryNotificationSentAt: string | null = null;
  let diaryNotificationVersion: number | null = null;
  if (hasPropertyType(dailyLogProperties, diaryNotificationSentPropertyName, "checkbox")) {
    diaryNotificationSent =
      getCheckboxFromProperty(properties[diaryNotificationSentPropertyName]) ?? false;
  }
  if (hasPropertyType(dailyLogProperties, diaryNotificationHashPropertyName, "rich_text")) {
    diaryNotificationHash =
      getPlainTextFromRichText(properties[diaryNotificationHashPropertyName]) || null;
  }
  if (hasPropertyType(dailyLogProperties, diaryNotificationSentAtPropertyName, "date")) {
    diaryNotificationSentAt =
      getDateTimeFromProperty(properties[diaryNotificationSentAtPropertyName]) ||
      getStringFromProperty(properties[diaryNotificationSentAtPropertyName]) ||
      null;
  }
  if (hasPropertyType(dailyLogProperties, diaryNotificationVersionPropertyName, "number")) {
    const notificationVersion = getNumberFromProperty(
      properties[diaryNotificationVersionPropertyName],
    );
    diaryNotificationVersion =
      typeof notificationVersion === "number" ? Math.trunc(notificationVersion) : null;
  }
  const mailId = getPlainTextFromRichText(properties["Mail ID"]);
  const mailMetadata = extractMailMetadataFromProperties(properties);
  const source = properties.Source?.select?.name ?? null;

  const expensesRelationIds = getRelationIdsFromProperty(
    properties[dailyLogExpensesPropertyNames.relation],
  );
  const expensesPropertyNames = getExpensesPropertyNames(env);
  const expenseEntries = (
    await Promise.all(
      expensesRelationIds.map(async (pageId) => {
        const response = await notionFetch(env, `/pages/${pageId}`);
        if (!response.ok) {
          const details = await getNotionErrorDetails(response);
          console.warn(
            `Notion API error in handleDailyLogRead.expenses: status=${details.status} page_id=${pageId} message=${details.message}`,
          );
          return null;
        }
        const expensePage = await response.json();
        const expenseProperties = expensePage.properties ?? {};
        const amount =
          getNumberFromProperty(expenseProperties[expensesPropertyNames.amount]) ?? 0;
        const merchant = getPlainTextFromRichText(
          expenseProperties[expensesPropertyNames.merchant],
        );
        const name = getPageTitleFromProperty(expensePage, expensesPropertyNames.name);
        const title = merchant || name || "Untitled";
        const url =
          typeof expensePage.url === "string"
            ? expensePage.url
            : buildNotionPageUrl(pageId);
        return {
          title,
          amount,
          url,
          createdTime:
            typeof expensePage.created_time === "string"
              ? expensePage.created_time
              : "",
        };
      }),
    )
  ).filter(
    (entry): entry is { title: string; amount: number; url: string; createdTime: string } =>
      Boolean(entry),
  );

  const expensesCount = expensesRelationIds.length;
  const calculatedTotal = expenseEntries.reduce((acc, entry) => acc + entry.amount, 0);
  const resolvedExpensesTotal =
    typeof expensesTotalRaw === "number" ? expensesTotalRaw : calculatedTotal;
  const sortedExpenses = expenseEntries.sort((a, b) => {
    if (b.amount !== a.amount) {
      return b.amount - a.amount;
    }
    const aTime = Date.parse(a.createdTime || "") || 0;
    const bTime = Date.parse(b.createdTime || "") || 0;
    return bTime - aTime;
  });
  const expensesTop = sortedExpenses.slice(0, 3).map((entry) => ({
    title: entry.title,
    amount: entry.amount,
    url: entry.url,
  }));
  const expensesRemaining = Math.max(expensesCount - expensesTop.length, 0);

  return new Response(
    JSON.stringify({
      found: true,
      target_date: targetDate,
      date,
      target_date_value: targetDateValue,
      page_id: page.id,
      title: getPageTitleFromProperty(page, TITLE_PROPERTIES.dailyLog),
      summary_text: summaryText,
      summary_html: summaryHtml,
      mail_id: mailId,
      mail_input_hash: mailMetadata.mailInputHash,
      mail_input_snapshot: mailMetadata.mailInputSnapshot,
      mail_sent_at: mailMetadata.mailSentAt,
      mail_version: mailMetadata.mailVersion,
      source,
      diary,
      meal_summary: mealSummary,
      "Meal Photos": mealPhotosRaw,
      meal_photos: mealPhotos,
      place,
      activity_summary: activitySummary,
      done_count: doneCount,
      done_tasks: doneTaskTitles,
      done_tasks_detail: doneTaskDetails,
      drop_count: dropCount,
      drop_tasks: dropTaskTitles,
      kcal,
      protein,
      fat,
      carb,
      sleep_start: sleepStart,
      sleep_end: sleepEnd,
      sleep_duration_min: sleepDurationMin,
      sleep_score: sleepScore,
      sleep_source: sleepSource,
      readiness_stars: readinessStars,
      readiness_hrv: readinessHrv,
      readiness_bpm: readinessBpm,
      baseline_hrv: baselineHrv,
      baseline_waking_bpm: baselineWakingBpm,
      sleep_heart_rate: sleepHeartRate,
      deep_duration_min: deepDurationMin,
      rem_duration_min: remDurationMin,
      sleep_analysis_jp: sleepAnalysisJp,
      today_condition_forecast_jp: todayConditionForecastJp,
      today_advice: todayAdvice,
      study_minutes: studyMinutes,
      study_sessions: studySessions,
      study_last_used_at: studyLastUsedAt,
      diary_input_hash: diaryInputHash,
      today_advice_input_hash: todayAdviceInputHash,
      diary_generated_at: diaryGeneratedAt,
      today_advice_generated_at: todayAdviceGeneratedAt,
      expenses_total: resolvedExpensesTotal,
      expenses: {
        total: resolvedExpensesTotal,
        count: expensesCount,
        top: expensesTop,
        remaining: expensesRemaining,
      },
      "Location summary (GPT)": locationSummaryFields.locationSummaryGpt,
      "Location summary": locationSummaryFields.locationSummaryLegacy,
      location_summary: locationSummaryFields.locationSummary,
      location_summary_source: locationSummaryFields.locationSummarySource,
      mood,
      notes,
      weight,
      page_url: pageUrl,
      diary_notification_sent: diaryNotificationSent,
      diary_notification_hash: diaryNotificationHash,
      diary_notification_sent_at: diaryNotificationSentAt,
      diary_notification_version: diaryNotificationVersion,
      weather: weatherLegacyText,
      weather_summary: weatherSummary,
      weather_location: weatherLocation,
      weather_temp_max_c: weatherTempMaxC,
      weather_temp_min_c: weatherTempMinC,
      weather_precip_probability_max: weatherPrecipProbabilityMax,
      weather_code: weatherCode,
      weather_retrieved_at: weatherRetrievedAt,
      weather_input_hash: weatherInputHash,
      weather_generated_at: weatherGeneratedAt,
    }),
    { headers: jsonHeaders },
  );
}

async function handleTaskPromoteConfirm(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const pageId = url.searchParams.get("id");
  if (!pageId) {
    return badRequest("missing id");
  }

  const html = `
    <h1>Promote task</h1>
    <p>Task ID: ${pageId}</p>
    <form method="post" action="/execute/tasks/promote">
      <input type="hidden" name="id" value="${pageId}" />
      <button type="submit">Promote to Do</button>
    </form>
  `;
  return createHtmlPage("Confirm Promote", html);
}

async function handleTaskPromoteExecute(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/tasks/promote");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  const { doStatus } = getTaskStatusConfig(env);
  await validateTasksDatabaseSchema(env);

  const formData = await request.formData();
  const pageId = formData.get("id");
  if (!pageId || typeof pageId !== "string") {
    return badRequest("missing id");
  }

  const jstDate = getJstDateString();
  const properties = {
    Status: createSelectProperty(doStatus),
    "Since Do": createDateProperty(jstDate),
  };

  const response = await notionFetch(env, `/pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify({ properties }),
  });

  if (!response.ok) {
    return notionErrorResponse(response, "handleTaskPromoteExecute");
  }

  return createHtmlPage("Promoted", "<p>Task promoted to Do.</p>");
}

async function handleDailyLogConfirm(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const targetDate = url.searchParams.get("target_date") ?? "";
  const title = url.searchParams.get("title") ?? "";
  const summaryText =
    url.searchParams.get("summary_text") ??
    url.searchParams.get("activity_summary") ??
    "";
  const summaryHtml = url.searchParams.get("summary_html") ?? "";
  const mailId = url.searchParams.get("mail_id") ?? "";
  const source = url.searchParams.get("source") ?? "automation";

  if (!targetDate || !title || !summaryText || !mailId) {
    return badRequest("missing required fields");
  }

  const html = `
    <h1>Daily Log Upsert</h1>
    <p>Target Date: ${targetDate}</p>
    <p>Title: ${title}</p>
    <p>Source: ${source}</p>
    <pre>${summaryText}</pre>
    <form method="post" action="/execute/api/daily_log/upsert">
      <input type="hidden" name="target_date" value="${targetDate}" />
      <input type="hidden" name="title" value="${title}" />
      <input type="hidden" name="summary_text" value="${summaryText}" />
      <input type="hidden" name="summary_html" value="${summaryHtml}" />
      <input type="hidden" name="mail_id" value="${mailId}" />
      <input type="hidden" name="source" value="${source}" />
      <button type="submit">Execute Upsert</button>
    </form>
  `;

  return createHtmlPage("Confirm Daily Log", html);
}

async function handleDailyLogExecute(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/upsert");
  }
  const contentType = request.headers.get("content-type") ?? "";
  let payload: Record<string, string> = {};

  if (contentType.includes("application/json")) {
    const parsed = await parseJsonBody(request);
    if (!parsed) {
      return badRequest("invalid json body");
    }
    payload = parsed as Record<string, string>;
  } else {
    const formData = await request.formData();
    formData.forEach((value, key) => {
      if (typeof value === "string") {
        payload[key] = value;
      }
    });
  }

  const proxyHeaders = new Headers(request.headers);
  proxyHeaders.set("content-type", "application/json; charset=utf-8");

  const proxyRequest = new Request(request.url, {
    method: "POST",
    headers: proxyHeaders,
    body: JSON.stringify(payload),
  });

  return handleDailyLogUpsert(proxyRequest, env);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = normalizePath(url.pathname);

    try {
      const routed = await dispatchRoute(path, {
        [ROUTES.INBOX]: () => handleInbox(request, env),
        [ROUTES.TASKS]: () => handleTasks(request, env),
        [ROUTES.TASKS_CLOSED]: () => handleTasksClosed(request, env),
        [ROUTES.DAILY_LOG_READ]: () => handleDailyLogRead(request, env),
        [ROUTES.DAILY_LOG_UPSERT]: () =>
          Promise.resolve(
            new Response(
              JSON.stringify({
                error: "use /execute/api/daily_log/upsert for updates",
              }),
              { status: 405, headers: jsonHeaders },
            ),
          ),
        [ROUTES.DAILY_LOG_CONFIRM_UPSERT]: () =>
          request.method === "GET"
            ? handleDailyLogConfirm(request)
            : Promise.resolve(methodNotAllowed("use GET /confirm/daily_log/upsert")),
        [ROUTES.DAILY_LOG_EXECUTE_UPSERT]: () => handleDailyLogExecute(request, env),
        [ROUTES.DAILY_LOG_INGEST_HEALTH]: () => handleDailyLogHealthIngest(request, env),
        [ROUTES.DAILY_LOG_INGEST_PHOTOS]: () => handleDailyLogPhotosIngest(request, env),
        [ROUTES.DAILY_LOG_INGEST_DAILY_LOG]: () => handleDailyLogIngest(request, env),
        [ROUTES.DAILY_LOG_INGEST_EXPENSES]: () => handleDailyLogExpensesIngest(request, env),
        [ROUTES.DAILY_LOG_INGEST_LOCATION]: () => handleDailyLogLocationIngest(request, env),
        [ROUTES.DAILY_LOG_GENERATE_DIARY]: () => handleDailyLogGenerateDiary(request, env),
        [ROUTES.DAILY_LOG_MARK_DIARY_NOTIFIED]: () =>
          handleDailyLogMarkDiaryNotified(request, env),
        [ROUTES.MOOD_NOTES_CONFIRM]: () => handleMoodNotesConfirm(request, env),
        [ROUTES.MOOD_NOTES_EXECUTE]: () => handleMoodNotesExecute(request, env),
        [ROUTES.MOOD_NOTES_INGEST]: () => handleMoodNotesIngest(request, env),
        [ROUTES.DAILY_LOG_ENSURE]: () => handleDailyLogEnsure(request, env),
        [ROUTES.TASKS_PROMOTE_CONFIRM]: () =>
          request.method === "GET"
            ? handleTaskPromoteConfirm(request)
            : Promise.resolve(methodNotAllowed("use GET /confirm/tasks/promote")),
        [ROUTES.TASKS_PROMOTE_EXECUTE]: () => handleTaskPromoteExecute(request, env),
        [ROUTES.HEALTH]: () => Promise.resolve(healthCheck()),
      });

      if (routed) {
        return routed;
      }

      return notFound();
    } catch (error) {
      if (error instanceof NotionApiError) {
        const bodySnippet =
          error.body.length > 4000
            ? `${error.body.slice(0, 4000)}...(truncated)`
            : error.body;
        const requestIdLog = error.requestId ? ` request_id=${error.requestId}` : "";
        console.error(
          `Notion API error: status=${error.status}${requestIdLog} ${error.message}`,
        );
        console.error(`Notion API response body: ${bodySnippet}`);
        const status = error.status >= 400 ? error.status : 500;
        return new Response(
          JSON.stringify({
            error: "notion_error",
            status,
            code: error.code ?? null,
            message: error.notionMessage ?? null,
            request_id: error.requestId ?? null,
            body: error.body,
          }),
          { status, headers: jsonHeaders },
        );
      }

      console.error("Unhandled error.", error);
      const message = error instanceof Error ? error.message : "Unknown error";
      return new Response(
        JSON.stringify({ error: "internal_error", message }),
        {
          status: 500,
          headers: jsonHeaders,
        },
      );
    }
  },
};

export const __test__ = {
  buildDailyLogProperties,
  extractMailMetadataFromProperties,
  normalizeFilesFromProperty,
  getFileUrlsFromProperty,
  resolveLocationSummaryFields,
  buildDailyLogUpsertProperties,
  getMealPhotosFilesCount,
  buildDailyLogUpsertDiagnostics,
  sanitizeMealPhotosPatchProperties,
  parseStudyPayload,
  applyStudyUpdateProperties,
  resolveExpensesAggregationWindow,
  parseExpenseTimestampMs,
};
