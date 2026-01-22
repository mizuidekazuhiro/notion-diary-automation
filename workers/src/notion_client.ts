export type NotionEnv = {
  NOTION_TOKEN: string;
};

export const NOTION_VERSION = "2022-06-28";

export async function notionFetch(
  env: NotionEnv,
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const url = `https://api.notion.com/v1/${path}`;
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
    const response = await notionFetch(env, `databases/${dbId}/query`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(await formatNotionError(response));
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
  try {
    const data = JSON.parse(rawText);
    const code = data.code ? ` (${data.code})` : "";
    const message = data.message ? `: ${data.message}` : "";
    return `Notion API error ${status}${code}${message}`;
  } catch {
    const trimmed = rawText.trim();
    return trimmed
      ? `Notion API error ${status}: ${trimmed}`
      : `Notion API error ${status}`;
  }
}
