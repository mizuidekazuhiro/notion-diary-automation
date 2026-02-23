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
  collectMealPhotosFromHealthPages,
  resolveIngestTargetDate,
} from "./domain/daily_log_ingest";
import {
  getTaskPropertyNames,
  TaskPropertyNameEnv,
} from "./config/task_property_names";
import { TITLE_PROPERTIES } from "./config/title_properties";

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

function buildDailyLogProperties(env: Env): ExpectedProperty[] {
  const dailyLogExpenses = getDailyLogExpensesPropertyNames(env);
  return [
    { name: TITLE_PROPERTIES.dailyLog, type: "title" },
    { name: "Date", type: "date" },
    { name: "Target Date", type: "date" },
    { name: "Activity Summary", type: "rich_text" },
    { name: "Diary", type: "rich_text" },
    { name: dailyLogExpenses.total, type: "number" },
    { name: "Meal summary", type: "rich_text" },
    { name: "Mail ID", type: "rich_text" },
    { name: "Mood", type: "select" },
    { name: "Source", type: "select" },
    { name: "Weight", type: "number" },
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
  for (const [key, value] of formData.entries()) {
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
    if (mode === "replace") {
      if (entry && entry !== existingText) {
        updatedNotesText = entry;
        notesUpdated = true;
      }
    } else if (entry && !shouldSkipMoodNotesAppend(existingText, notesValue, sourceUrl)) {
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
    return new Response(
      JSON.stringify({
        ok: true,
        updated: false,
        reason: "no changes",
        target_date: targetDate,
        found: Boolean(existingPage),
        page_id: existingPage?.id ?? null,
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

  return new Response(
    JSON.stringify({
      ok: true,
      updated: true,
      target_date: targetDate,
      page_id: pageId,
    }),
    { headers: jsonHeaders },
  );
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
    const schema = properties[property.name];
    if (!schema) {
      missing.push(property.name);
      return;
    }
    if (schema.type !== property.type) {
      mismatched.push(`${property.name} (expected ${property.type}, got ${schema.type})`);
    }
  });

  Object.entries(selectOptionRequirements).forEach(([propertyName, requiredOptions]) => {
    const schema = properties[propertyName];
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
  const schema = properties[name];
  return Boolean(schema && schema.type === type);
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
};

type DailyLogHealthPropertyNames = {
  protein: string;
  fat: string;
  carb: string;
  kcal: string;
  weight: string;
  mealPhoto: string;
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

function getPlainTextFromRichText(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const richText = property.rich_text;
  if (!Array.isArray(richText)) {
    return "";
  }
  return richText
    .map((item: { plain_text?: string }) => item.plain_text ?? "")
    .join("");
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

function shouldSkipMoodNotesAppend(
  existingText: string,
  notes: string,
  sourceUrl?: string,
): boolean {
  const trimmedNotes = notes.trim();
  if (!existingText) {
    return false;
  }
  if (trimmedNotes && sourceUrl) {
    return existingText.includes(trimmedNotes) && existingText.includes(sourceUrl);
  }
  if (trimmedNotes) {
    return existingText.includes(trimmedNotes);
  }
  if (sourceUrl) {
    return existingText.includes(sourceUrl);
  }
  return false;
}

function getNumberFromProperty(
  property: Record<string, any> | undefined,
): number | null {
  if (!property || typeof property.number !== "number") {
    return null;
  }
  return property.number;
}

function normalizeFilesFromProperty(
  property: Record<string, any> | undefined,
): Array<Record<string, any>> {
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
    .filter((item): item is Record<string, any> => Boolean(item));
}

function getFileUrlsFromProperty(
  property: Record<string, any> | undefined,
): string[] {
  if (!property || !Array.isArray(property.files)) {
    return [];
  }
  return property.files
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
  if (!pageId) {
    const queryResponse = await notionFetch(
      env,
      `/databases/${env.DAILY_LOG_DB_ID}/query`,
      {
        method: "POST",
        body: JSON.stringify({
          page_size: 1,
          filter: {
            property: "Target Date",
            date: { equals: targetDate },
          },
        }),
      },
    );

    if (!queryResponse.ok) {
      return notionErrorResponse(queryResponse, "handleDailyLogUpsert.query");
    }

    const queryData = await queryResponse.json();
    existingPage = (queryData.results ?? [])[0] ?? null;
  }

  const properties: Record<string, any> = {
    [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
    "Target Date": createDateProperty(targetDate),
    Date: createDateProperty(targetDate),
    "Activity Summary": createRichTextProperty(summaryText),
    "Mail ID": createRichTextProperty(mailId),
    Source: createSelectProperty(source),
  };

  let resultResponse: Response;
  if (pageId || existingPage) {
    const resolvedPageId = pageId ?? existingPage?.id;
    resultResponse = await notionFetch(env, `/pages/${resolvedPageId}`, {
      method: "PATCH",
      body: JSON.stringify({ properties }),
    });
  } else {
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
      `Notion API error in handleDailyLogUpsert.upsert: status=${details.status}${requestIdLog}${codeLog} message=${messageLog}`,
    );
    console.error(
      `DailyLog upsert properties: ${Object.keys(properties).join(", ")}`,
    );
    return notionErrorResponseFromDetails(details);
  }

  const resolvedPageId = pageId ?? (existingPage ? existingPage.id : undefined);
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
  const protein = getNumberFromProperty(healthProps[healthPropertyNames.protein]);
  const fat = getNumberFromProperty(healthProps[healthPropertyNames.fat]);
  const carb = getNumberFromProperty(healthProps[healthPropertyNames.carb]);
  const kcal = getNumberFromProperty(healthProps[healthPropertyNames.kcal]);
  const weight = getNumberFromProperty(healthProps[healthPropertyNames.weight]);
  const mealPhotos = normalizeFilesFromProperty(
    healthProps[healthPropertyNames.mealPhoto],
  );
  const mealSummary = formatMealSummary(protein, fat, carb, kcal, weight);

  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));
  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
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
    updateProperties[dailyLogHealthPropertyNames.mealPhoto] =
      createFilesProperty(mealPhotos);
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

  const dailyLogQuery = await notionFetch(
    env,
    `/databases/${env.DAILY_LOG_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 1,
        filter: {
          property: "Target Date",
          date: { equals: targetDate },
        },
      }),
    },
  );

  if (!dailyLogQuery.ok) {
    return notionErrorResponse(dailyLogQuery, "handleDailyLogHealthIngest.queryDaily");
  }

  const dailyLogData = await dailyLogQuery.json();
  const existingPage = (dailyLogData.results ?? [])[0] ?? null;

  let resultResponse: Response;
  if (existingPage) {
    resultResponse = await notionFetch(env, `/pages/${existingPage.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: updateProperties }),
    });
  } else {
    const title = `Daily Log｜${targetDate}`;
    const properties = {
      [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
      "Target Date": createDateProperty(targetDate),
      Date: createDateProperty(targetDate),
      ...updateProperties,
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
): Promise<{ pageId: string } | { error: Response }> {
  const dailyLogQuery = await notionFetch(
    env,
    `/databases/${env.DAILY_LOG_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 1,
        filter: {
          property: "Target Date",
          date: { equals: targetDate },
        },
      }),
    },
  );

  if (!dailyLogQuery.ok) {
    return {
      error: await notionErrorResponse(dailyLogQuery, `${logContext}.queryDaily`),
    };
  }

  const dailyLogData = await dailyLogQuery.json();
  const existingPage = (dailyLogData.results ?? [])[0] ?? null;

  let resultResponse: Response;
  if (existingPage) {
    resultResponse = await notionFetch(env, `/pages/${existingPage.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: updateProperties }),
    });
  } else {
    const title = `Daily Log｜${targetDate}`;
    const properties = {
      [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
      "Target Date": createDateProperty(targetDate),
      Date: createDateProperty(targetDate),
      ...updateProperties,
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
  return { pageId };
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

  const healthPropertyNames = getHealthPropertyNames(env);
  const dailyLogHealthPropertyNames = getDailyLogHealthPropertyNames(env);
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
  const mealPhotos = collectMealPhotosFromHealthPages(
    healthPages,
    healthPropertyNames.mealPhoto,
    normalizeFilesFromProperty,
  );
  if (!mealPhotos.length) {
    return new Response(
      JSON.stringify({
        ok: true,
        target_date: targetDate,
        found: false,
        updated: false,
        reason: "no photos",
      }),
      { headers: jsonHeaders },
    );
  }

  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));
  const dailyLogProperties = await getDatabaseProperties(env, env.DAILY_LOG_DB_ID);
  let updateProperties: Record<string, any> = {};
  if (hasPropertyType(dailyLogProperties, dailyLogHealthPropertyNames.mealPhoto, "files")) {
    updateProperties = buildPhotoOnlyUpdateProperties(
      dailyLogHealthPropertyNames.mealPhoto,
      createFilesProperty(mealPhotos),
    );
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


function getDateTimeFromProperty(property: Record<string, any> | undefined): string {
  const value = property?.date?.start;
  return typeof value === "string" ? value : "";
}

function getStringFromProperty(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const richText = getPlainTextFromRichText(property);
  if (richText) {
    return richText;
  }
  const titleText = getPlainTextFromTitle(property);
  if (titleText) {
    return titleText;
  }
  if (typeof property.select?.name === "string") {
    return property.select.name;
  }
  return "";
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
    const body = await response.text();
    console.warn(`Location summary GPT failed status=${response.status} body=${body.slice(0, 500)}`);
    return fallback;
  }

  const data = await response.json();
  const content = data?.choices?.[0]?.message?.content;
  if (typeof content !== "string") {
    return fallback;
  }

  try {
    const parsed = JSON.parse(content) as LocationGptOutput;
    const text = typeof parsed.location_summary_text === "string" ? parsed.location_summary_text.trim() : "";
    if (!text) {
      return fallback;
    }
    const primary =
      typeof parsed.primary_place_label === "string" && parsed.primary_place_label.trim()
        ? parsed.primary_place_label.trim()
        : fallback.primary_place_label;
    const stats = typeof parsed.stats === "object" && parsed.stats ? parsed.stats : fallback.stats;
    return {
      location_summary_text: text,
      primary_place_label: primary,
      stats: {
        ...fallback.stats,
        ...stats,
        data_quality_notes: dataQualityNotes,
      },
    } as LocationSummaryResult;
  } catch (error) {
    console.warn("Location summary: failed to parse GPT JSON, using fallback.", error);
    return fallback;
  }
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

  const payload = await parseJsonBody(request);
  if (!payload) {
    return badRequest("invalid json body");
  }

  const targetDateResult = resolveIngestTargetDate(payload);
  const windowStartHour = parseIntEnv(env.WINDOW_START_HOUR, 5);
  const window = resolveLocationWindow(new Date(), windowStartHour);
  const diaryDate = targetDateResult.ok ? targetDateResult.targetDate : window.diaryDate;
  const dailyLogDateProp = getDailyLogDatePropertyName(env);

        skipped: "location_summary_skipped_ingest_phase_a",
  console.info("location summary skipped (ingest)", { target_date: diaryDate, page_id: pageId });

      skipped: "location_summary_skipped_ingest_phase_a",
      date: { equals: diaryDate },
    },
    sorts: [{ timestamp: "last_edited_time", direction: "descending" }],
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
        timestamp: "created_time",
        created_time: { on_or_after: startJst },
      },
      {
        timestamp: "created_time",
        created_time: { before: endJst },
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
    const timestampMs = parseExpenseCreatedTimeMs(page);
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

  const dailyLogQuery = await notionFetch(
    env,
    `/databases/${env.DAILY_LOG_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 1,
        filter: {
          property: "Target Date",
          date: { equals: targetDate },
        },
      }),
    },
  );

  if (!dailyLogQuery.ok) {
    return notionErrorResponse(dailyLogQuery, "handleDailyLogExpensesIngest.queryDaily");
  }

  const dailyLogData = await dailyLogQuery.json();
  const existingPage = (dailyLogData.results ?? [])[0] ?? null;

  let resultResponse: Response;
  if (existingPage) {
    resultResponse = await notionFetch(env, `/pages/${existingPage.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties: updateProperties }),
    });
  } else {
    const title = `Daily Log｜${targetDate}`;
    const properties = {
      [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
      "Target Date": createDateProperty(targetDate),
      Date: createDateProperty(targetDate),
      ...updateProperties,
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

  const queryResponse = await notionFetch(
    env,
    `/databases/${env.DAILY_LOG_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 1,
        filter: {
          property: "Target Date",
          date: { equals: targetDate },
        },
      }),
    },
  );

  if (!queryResponse.ok) {
    return notionErrorResponse(queryResponse, "handleDailyLogEnsure.query");
  }

  const queryData = await queryResponse.json();
  const existingPage = (queryData.results ?? [])[0];
  if (existingPage) {
    return new Response(JSON.stringify({ ok: true, page_id: existingPage.id }), {
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
  return new Response(JSON.stringify({ ok: true, page_id: pageId }), {
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

  return missingProperties.length === 1 && missingProperties[0] === "Location summary";
}

async function handleDailyLogRead(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return methodNotAllowed();
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, buildDailyLogProperties(env));

  const url = new URL(request.url);
  const targetDate = url.searchParams.get("date")?.trim() ?? "";
  if (!targetDate) {
    return badRequest("missing date");
  }
  if (!isValidDateString(targetDate)) {
    return badRequest("invalid date format");
  }

  const queryResponse = await notionFetch(
    env,
    `/databases/${env.DAILY_LOG_DB_ID}/query`,
    {
      method: "POST",
      body: JSON.stringify({
        page_size: 1,
        filter: {
          property: "Target Date",
          date: { equals: targetDate },
        },
      }),
    },
  );

  if (!queryResponse.ok) {
    return notionErrorResponse(queryResponse, "handleDailyLogRead.query");
  }

  const queryData = await queryResponse.json();
  const page = (queryData.results ?? [])[0];
  if (!page) {
    return new Response(JSON.stringify({ found: false, target_date: targetDate }), {
      headers: jsonHeaders,
    });
  }

  const properties = page.properties ?? {};
  const summaryText = getPlainTextFromRichText(properties["Activity Summary"]);
  const summaryHtml = getPlainTextFromRichText(properties.Diary);
  const diary = getPlainTextFromRichText(properties.Diary) || null;
  const mealSummary = getPlainTextFromRichText(properties["Meal summary"]) || null;
  const mealPhotos = getFileUrlsFromProperty(properties["Meal Photos"]);
  const dailyLogExpensesPropertyNames = getDailyLogExpensesPropertyNames(env);
  const expensesTotalRaw =
    typeof properties[dailyLogExpensesPropertyNames.total]?.number === "number"
      ? properties[dailyLogExpensesPropertyNames.total].number
      : null;
  const locationSummary = getPlainTextFromRichText(properties["Location summary"]) || null;
  const mood = properties.Mood?.select?.name ?? null;
  const weight =
    typeof properties.Weight?.number === "number" ? properties.Weight.number : null;
  const mailId = getPlainTextFromRichText(properties["Mail ID"]);
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
      page_id: page.id,
      title: getPageTitleFromProperty(page, TITLE_PROPERTIES.dailyLog),
      summary_text: summaryText,
      summary_html: summaryHtml,
      mail_id: mailId,
      source,
      diary,
      meal_summary: mealSummary,
      meal_photos: mealPhotos,
      expenses_total: resolvedExpensesTotal,
      expenses: {
        total: resolvedExpensesTotal,
        count: expensesCount,
        top: expensesTop,
        remaining: expensesRemaining,
      },
      location_summary: locationSummary,
      mood,
      weight,
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
      if (path === "/api/inbox") {
        return await handleInbox(request, env);
      }
      if (path === "/api/tasks") {
        return await handleTasks(request, env);
      }
      if (path === "/api/tasks/closed") {
        return await handleTasksClosed(request, env);
      }
      if (path === "/api/daily_log") {
        return await handleDailyLogRead(request, env);
      }
      if (path === "/api/daily_log/upsert") {
        return new Response(
          JSON.stringify({
            error: "use /execute/api/daily_log/upsert for updates",
          }),
          { status: 405, headers: jsonHeaders },
        );
      }
      if (path === "/confirm/daily_log/upsert" && request.method === "GET") {
        return await handleDailyLogConfirm(request);
      }
      if (path === "/execute/api/daily_log/upsert") {
        return await handleDailyLogExecute(request, env);
      }
      if (path === "/execute/api/daily_log/ingest_health") {
        return await handleDailyLogHealthIngest(request, env);
      }
      if (path === "/execute/api/daily_log/ingest_photos") {
        return await handleDailyLogPhotosIngest(request, env);
      }
      if (path === "/execute/api/daily_log/ingest_daily_log") {
        return await handleDailyLogIngest(request, env);
      }
      if (path === "/execute/api/daily_log/ingest_expenses") {
        return await handleDailyLogExpensesIngest(request, env);
      }
      if (path === "/execute/api/daily_log/ingest_location") {
        return await handleDailyLogLocationIngest(request, env);
      }
      if (path === "/confirm/mood-notes") {
        return await handleMoodNotesConfirm(request, env);
      }
      if (path === "/execute/mood-notes") {
        return await handleMoodNotesExecute(request, env);
      }
      if (path === "/ingest/mood-notes") {
        return await handleMoodNotesIngest(request, env);
      }
      if (path === "/execute/api/daily_log/ensure") {
        return await handleDailyLogEnsure(request, env);
      }
      if (path === "/confirm/tasks/promote" && request.method === "GET") {
        return await handleTaskPromoteConfirm(request);
      }
      if (path === "/execute/tasks/promote") {
        return await handleTaskPromoteExecute(request, env);
      }
      if (path === "/health") {
        return healthCheck();
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
