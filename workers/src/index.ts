import {
  addDaysToJstDate,
  getJstDateString,
  getJstYesterdayString,
  getJstDateStringFromDateTime,
  isValidDateString,
  formatJstDateTime,
} from "./date_utils";
import { updateDailyLogTaskRelations } from "./daily_log_task_relations";
import {
  getNotionErrorDetails,
  NotionApiError,
  notionFetch,
  queryDatabaseAll,
} from "./notion_client";
import {
  getTaskPropertyNames,
  TaskPropertyNameEnv,
} from "./task_property_names";
import { TITLE_PROPERTIES } from "./title_properties";

interface Env {
  NOTION_TOKEN: string;
  INBOX_DB_ID: string;
  TASK_DB_ID: string;
  DAILY_LOG_DB_ID: string;
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
}

type NotionPropertyType =
  | "title"
  | "rich_text"
  | "number"
  | "select"
  | "date"
  | "checkbox"
  | "relation"
  | "rollup";

type ExpectedProperty = {
  name: string;
  type: NotionPropertyType;
};

type SchemaCache = Record<string, boolean>;

const schemaCache: SchemaCache = {};

const DAILY_LOG_PROPERTIES: ExpectedProperty[] = [
  { name: TITLE_PROPERTIES.dailyLog, type: "title" },
  { name: "Date", type: "date" },
  { name: "Target Date", type: "date" },
  { name: "Activity Summary", type: "rich_text" },
  { name: "Diary", type: "rich_text" },
  { name: "Expenses total", type: "number" },
  { name: "Location summary", type: "rich_text" },
  { name: "Meal summary", type: "rich_text" },
  { name: "Mail ID", type: "rich_text" },
  { name: "Mood", type: "select" },
  { name: "Source", type: "select" },
  { name: "Weight", type: "number" },
];

const DAILY_LOG_RELATION_PROPERTIES: ExpectedProperty[] = [
  { name: "Date", type: "date" },
  { name: "Done Tasks", type: "relation" },
  { name: "Drop Tasks", type: "relation" },
];

const BODY_CHUNK_LENGTH = 1800;

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

const INBOX_PROPERTIES: ExpectedProperty[] = [
  { name: TITLE_PROPERTIES.inbox, type: "title" },
];

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
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

function normalizePath(path: string): string {
  if (path.length <= 1) {
    return path;
  }
  return path.replace(/\/+$/, "");
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

  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, DAILY_LOG_PROPERTIES);

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

async function handleDailyLogEnsure(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed("use POST /execute/api/daily_log/ensure");
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, DAILY_LOG_PROPERTIES);

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

async function handleDailyLogRead(request: Request, env: Env): Promise<Response> {
  if (request.method !== "GET") {
    return methodNotAllowed();
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, DAILY_LOG_PROPERTIES);

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
  const mailId = getPlainTextFromRichText(properties["Mail ID"]);
  const source = properties.Source?.select?.name ?? null;

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
