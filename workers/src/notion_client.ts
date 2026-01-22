export type NotionEnv = {
  NOTION_TOKEN: string;
};

export const NOTION_VERSION = "2022-06-28";

export type NotionErrorDetails = {
  status: number;
  body: string;
  message: string;
  code?: string;
  notionMessage?: string;
  requestId?: string;
};

export class NotionApiError extends Error {
  status: number;
  body: string;
  code?: string;
  requestId?: string;
  notionMessage?: string;

  constructor(details: NotionErrorDetails) {
    super(details.message);
    this.name = "NotionApiError";
    this.status = details.status;
    this.body = details.body;
    this.code = details.code;
    this.requestId = details.requestId;
    this.notionMessage = details.notionMessage;
  }
}

const NOTION_BASE = "https://api.notion.com/v1";

type ParsedNotionError = {
  code?: string;
  message?: string;
  requestId?: string;
};

function parseNotionErrorBody(rawText: string): ParsedNotionError {
  try {
    const data = JSON.parse(rawText);
    return {
      code: typeof data.code === "string" ? data.code : undefined,
      message: typeof data.message === "string" ? data.message : undefined,
      requestId:
        typeof data.request_id === "string"
          ? data.request_id
          : typeof data.requestId === "string"
            ? data.requestId
            : undefined,
    };
  } catch {
    return {};
  }
}

function normalizeNotionPath(path: string): string {
  const trimmed = path.trim();
  if (/^https?:\/\//i.test(trimmed)) {
    throw new Error(`Notion path must be relative, got full URL: ${trimmed}`);
  }
  if (trimmed.startsWith("/v1/") || trimmed === "/v1") {
    throw new Error(`Notion path must not include /v1: ${trimmed}`);
  }
  if (trimmed.startsWith("v1/") || trimmed === "v1") {
    throw new Error(`Notion path must not include v1: ${trimmed}`);
  }
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

export async function notionFetch(
  env: NotionEnv,
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const normalizedPath = normalizeNotionPath(path);
  const url = `${NOTION_BASE}${normalizedPath}`;
  const method = (options.method ?? "GET").toUpperCase();
  console.log(`Notion request: ${method} ${url} path=${normalizedPath}`);
  return fetch(url, {
    ...options,
    headers: {
      Authorization: `Bearer ${env.NOTION_TOKEN}`,
      "Notion-Version": NOTION_VERSION,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
}

function formatNotionErrorBody(status: number, rawText: string): string {
  const parsed = parseNotionErrorBody(rawText);
  if (parsed.code || parsed.message) {
    const code = parsed.code ? ` (${parsed.code})` : "";
    const message = parsed.message ? `: ${parsed.message}` : "";
    return `Notion API error ${status}${code}${message}`;
  }
  const trimmed = rawText.trim();
  return trimmed
    ? `Notion API error ${status}: ${trimmed}`
    : `Notion API error ${status}`;
}

export async function getNotionErrorDetails(
  response: Response,
): Promise<NotionErrorDetails> {
  const status = response.status;
  const rawText = await response.text();
  const parsed = parseNotionErrorBody(rawText);
  const message = formatNotionErrorBody(status, rawText);
  return {
    status,
    body: rawText,
    message,
    code: parsed.code,
    notionMessage: parsed.message,
    requestId: parsed.requestId,
  };
}

export async function queryDatabaseAll(
  env: NotionEnv,
  dbId: string,
  filter: Record<string, any>,
): Promise<Record<string, any>[]> {
  const results: Record<string, any>[] = [];
  let hasMore = true;
  let startCursor: string | undefined;

  while (hasMore) {
    const body: Record<string, any> = {
      page_size: 100,
      filter,
    };
    if (startCursor) {
      body.start_cursor = startCursor;
    }
    const response = await notionFetch(env, `/databases/${dbId}/query`, {
      method: "POST",
      body: JSON.stringify(body),
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

export async function formatNotionError(response: Response): Promise<string> {
  const status = response.status;
  const rawText = await response.text();
  return formatNotionErrorBody(status, rawText);
}
