import {
  addDaysToJstDate,
  getJstDateString,
  getJstYesterdayString,
  isValidDateString,
} from "./date_utils";
import { updateDailyLogTaskRelations } from "./daily_log_task_relations";
import { formatNotionError, notionFetch, queryDatabaseAll } from "./notion_client";
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
  { name: "Notes", type: "rich_text" },
  { name: "Source", type: "select" },
  { name: "Weight", type: "number" },
];

const DAILY_LOG_RELATION_PROPERTIES: ExpectedProperty[] = [
  { name: "Date", type: "date" },
  { name: "Done Tasks", type: "relation" },
  { name: "Drop Tasks", type: "relation" },
];

const TASK_PROPERTIES: ExpectedProperty[] = [
  { name: "Status", type: "select" },
  { name: "Since Do", type: "date" },
  { name: "Priority", type: "select" },
  { name: TITLE_PROPERTIES.tasks, type: "title" },
  { name: "Done date", type: "date" },
  { name: "Drop date", type: "date" },
];

const INBOX_PROPERTIES: ExpectedProperty[] = [
  { name: TITLE_PROPERTIES.inbox, type: "title" },
];

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
};

function unauthorized(): Response {
  return new Response(JSON.stringify({ error: "unauthorized" }), {
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

function methodNotAllowed(): Response {
  return new Response(JSON.stringify({ error: "method not allowed" }), {
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

  const response = await notionFetch(env, `databases/${dbId}`);
  if (!response.ok) {
    throw new Error(await formatNotionError(response));
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
    env.TASK_STATUS_DROPPED || env.TASK_STATUS_DROP_VALUE || "Dropped";
  const somedayStatus = env.TASK_STATUS_SOMEDAY || "Someday";
  const requireExtraOptions = parseBooleanEnv(env.REQUIRE_STATUS_EXTRA_OPTIONS);
  return { doStatus, doneStatus, droppedStatus, somedayStatus, requireExtraOptions };
}

function getTaskStatusOptionRequirements(env: Env): Record<string, string[]> {
  const { doStatus, doneStatus, droppedStatus, requireExtraOptions } =
    getTaskStatusConfig(env);
  const extraOptions = requireExtraOptions ? ["Drop", "Someday"] : [];
  return {
    Status: [doStatus, doneStatus, droppedStatus, ...extraOptions],
  };
}

async function validateTasksDatabaseSchema(env: Env): Promise<void> {
  await validateDatabaseSchema(
    env,
    env.TASK_DB_ID,
    TASK_PROPERTIES,
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
  return {
    rich_text: content
      ? [
          {
            text: { content },
          },
        ]
      : [],
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

async function requireBearerToken(request: Request, env: Env): Promise<Response | null> {
  if (!env.WORKERS_BEARER_TOKEN) {
    return null;
  }
  const authHeader = request.headers.get("authorization");
  if (!authHeader) {
    return unauthorized();
  }
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (token !== env.WORKERS_BEARER_TOKEN) {
    return unauthorized();
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

  const response = await notionFetch(env, `databases/${env.INBOX_DB_ID}/query`, {
    method: "POST",
    body: JSON.stringify({ page_size: 50 }),
  });
  if (!response.ok) {
    return new Response(await formatNotionError(response), { status: response.status });
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

  const response = await notionFetch(env, `databases/${env.TASK_DB_ID}/query`, {
    method: "POST",
    body: JSON.stringify({
      page_size: 100,
      filter: {
        or: [
          { property: "Status", select: { equals: doStatus } },
          { property: "Status", select: { equals: somedayStatus } },
        ],
      },
    }),
  });

  if (!response.ok) {
    return new Response(await formatNotionError(response), { status: response.status });
  }

  const data = await response.json();
  const origin = new URL(request.url).origin;
  const results = (data.results ?? []).map((page: Record<string, any>) => {
    const status = page.properties?.Status?.select?.name ?? null;
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

  const url = new URL(request.url);
  const dateParam = url.searchParams.get("date");
  let targetDate = dateParam?.trim();
  if (!targetDate) {
    targetDate = getJstYesterdayString();
  } else if (!isValidDateString(targetDate)) {
    return badRequest("invalid date format");
  }

  const startJst = `${targetDate}T00:00:00+09:00`;
  const nextDate = addDaysToJstDate(targetDate, 1);
  const endJst = `${nextDate}T00:00:00+09:00`;

  const donePages = await queryDatabaseAll(env, env.TASK_DB_ID, {
    property: "Done date",
    date: {
      on_or_after: startJst,
      before: endJst,
    },
  });

  const dropPages = await queryDatabaseAll(env, env.TASK_DB_ID, {
    property: "Drop date",
    date: {
      on_or_after: startJst,
      before: endJst,
    },
  });

  const done = donePages.map((page: Record<string, any>) => ({
    page_id: page.id,
    title: getPageTitleFromProperty(page, TITLE_PROPERTIES.tasks),
    priority: page.properties?.Priority?.select?.name ?? null,
    done_date: page.properties?.["Done date"]?.date?.start ?? null,
  }));

  const drop = dropPages.map((page: Record<string, any>) => ({
    page_id: page.id,
    title: getPageTitleFromProperty(page, TITLE_PROPERTIES.tasks),
    priority: page.properties?.Priority?.select?.name ?? null,
    drop_date: page.properties?.["Drop date"]?.date?.start ?? null,
  }));

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
    }),
    { headers: jsonHeaders },
  );
}

async function handleDailyLogUpsert(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed();
  }
  const authError = await requireBearerToken(request, env);
  if (authError) {
    return authError;
  }

  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, DAILY_LOG_PROPERTIES);

  const payload = await request.json();
  const {
    target_date: targetDate,
    title,
    activity_summary: activitySummary,
    mail_id: mailId,
    source,
    data_json: dataJson,
  } = payload ?? {};

  if (!targetDate || !title || !activitySummary || !mailId || !source) {
    return badRequest("missing required fields");
  }

  const queryResponse = await notionFetch(
    env,
    `databases/${env.DAILY_LOG_DB_ID}/query`,
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
    return new Response(await formatNotionError(queryResponse), {
      status: queryResponse.status,
    });
  }

  const queryData = await queryResponse.json();
  const existingPage = (queryData.results ?? [])[0];

  const properties: Record<string, any> = {
    [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
    "Target Date": createDateProperty(targetDate),
    Date: createDateProperty(targetDate),
    "Activity Summary": createRichTextProperty(activitySummary),
    Diary: createRichTextProperty(""),
    "Mail ID": createRichTextProperty(mailId),
    Source: createSelectProperty(source),
  };

  if (dataJson) {
    properties.Notes = createRichTextProperty(dataJson);
  }

  let resultResponse: Response;
  if (existingPage) {
    resultResponse = await notionFetch(env, `pages/${existingPage.id}`, {
      method: "PATCH",
      body: JSON.stringify({ properties }),
    });
  } else {
    resultResponse = await notionFetch(env, "pages", {
      method: "POST",
      body: JSON.stringify({
        parent: { database_id: env.DAILY_LOG_DB_ID },
        properties,
      }),
    });
  }

  if (!resultResponse.ok) {
    return new Response(await formatNotionError(resultResponse), {
      status: resultResponse.status,
    });
  }

  await validateTasksDatabaseSchema(env);
  await validateDatabaseSchema(env, env.DAILY_LOG_DB_ID, DAILY_LOG_RELATION_PROPERTIES);

  await updateDailyLogTaskRelations(env, targetDate);

  return new Response(JSON.stringify({ ok: true }), {
    headers: jsonHeaders,
  });
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
    return methodNotAllowed();
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

  const response = await notionFetch(env, `pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify({ properties }),
  });

  if (!response.ok) {
    return new Response(await formatNotionError(response), { status: response.status });
  }

  return createHtmlPage("Promoted", "<p>Task promoted to Do.</p>");
}

async function handleDailyLogConfirm(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const targetDate = url.searchParams.get("target_date") ?? "";
  const title = url.searchParams.get("title") ?? "";
  const activitySummary = url.searchParams.get("activity_summary") ?? "";
  const mailId = url.searchParams.get("mail_id") ?? "";
  const source = url.searchParams.get("source") ?? "automation";

  if (!targetDate || !title || !activitySummary || !mailId) {
    return badRequest("missing required fields");
  }

  const html = `
    <h1>Daily Log Upsert</h1>
    <p>Target Date: ${targetDate}</p>
    <p>Title: ${title}</p>
    <p>Source: ${source}</p>
    <pre>${activitySummary}</pre>
    <form method="post" action="/execute/api/daily_log/upsert">
      <input type="hidden" name="target_date" value="${targetDate}" />
      <input type="hidden" name="title" value="${title}" />
      <input type="hidden" name="activity_summary" value="${activitySummary}" />
      <input type="hidden" name="mail_id" value="${mailId}" />
      <input type="hidden" name="source" value="${source}" />
      <button type="submit">Execute Upsert</button>
    </form>
  `;

  return createHtmlPage("Confirm Daily Log", html);
}

async function handleDailyLogExecute(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") {
    return methodNotAllowed();
  }
  const contentType = request.headers.get("content-type") ?? "";
  let payload: Record<string, string> = {};

  if (contentType.includes("application/json")) {
    payload = await request.json();
  } else {
    const formData = await request.formData();
    formData.forEach((value, key) => {
      if (typeof value === "string") {
        payload[key] = value;
      }
    });
  }

  const proxyRequest = new Request(request.url, {
    method: "POST",
    headers: request.headers,
    body: JSON.stringify(payload),
  });

  return handleDailyLogUpsert(proxyRequest, env);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

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
      return new Response(JSON.stringify({ error: (error as Error).message }), {
        status: 500,
        headers: jsonHeaders,
      });
    }
  },
};
