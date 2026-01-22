import { addDaysToJstDate, getJstDateString } from "./date_utils";
import { formatNotionError, notionFetch, queryDatabaseAll } from "./notion_client";
import { TITLE_PROPERTIES } from "./title_properties";

export type DailyLogTaskRelationEnv = {
  NOTION_TOKEN: string;
  TASK_DB_ID: string;
  DAILY_LOG_DB_ID: string;
  TASK_STATUS_DONE?: string;
  TASK_STATUS_DROPPED?: string;
  TASK_STATUS_DROP_VALUE?: string;
};

export type DailyLogTaskRelationResult = {
  target_date: string;
  range: {
    start_jst: string;
    end_jst: string;
  };
  daily_log_page_id: string;
  created: boolean;
  done_count: number;
  drop_count: number;
};

const DEFAULT_DONE_STATUS = "Done";
const DEFAULT_DROP_STATUS = "Dropped";

function createTitleProperty(title: string) {
  return {
    title: [
      {
        text: { content: title },
      },
    ],
  };
}

function createDateProperty(date: string) {
  return {
    date: date ? { start: date } : null,
  };
}

function createRelationProperty(ids: string[]) {
  return {
    relation: ids.map((id) => ({ id })),
  };
}

function buildYesterdayRange(targetDate: string) {
  const yesterday = addDaysToJstDate(targetDate, -1);
  const startJst = `${yesterday}T00:00:00+09:00`;
  const endJst = `${targetDate}T00:00:00+09:00`;
  return { startJst, endJst };
}

async function fetchTaskIdsByStatus(
  env: DailyLogTaskRelationEnv,
  status: string,
  dateProperty: string,
  range: { startJst: string; endJst: string },
): Promise<string[]> {
  const pages = await queryDatabaseAll(env, env.TASK_DB_ID, {
    and: [
      { property: "Status", select: { equals: status } },
      {
        property: dateProperty,
        date: {
          on_or_after: range.startJst,
          before: range.endJst,
        },
      },
    ],
  });

  return pages.map((page: Record<string, any>) => page.id);
}

async function findOrCreateDailyLogPage(
  env: DailyLogTaskRelationEnv,
  targetDate: string,
): Promise<{ pageId: string; created: boolean }> {
  const queryResponse = await notionFetch(
    env,
    `databases/${env.DAILY_LOG_DB_ID}/query`,
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
    throw new Error(await formatNotionError(queryResponse));
  }

  const queryData = await queryResponse.json();
  const existingPage = (queryData.results ?? [])[0];
  if (existingPage) {
    return { pageId: existingPage.id, created: false };
  }

  const title = `${targetDate} Daily Log`;
  const createResponse = await notionFetch(env, "pages", {
    method: "POST",
    body: JSON.stringify({
      parent: { database_id: env.DAILY_LOG_DB_ID },
      properties: {
        [TITLE_PROPERTIES.dailyLog]: createTitleProperty(title),
        Date: createDateProperty(targetDate),
      },
    }),
  });

  if (!createResponse.ok) {
    throw new Error(await formatNotionError(createResponse));
  }

  const createdPage = await createResponse.json();
  return { pageId: createdPage.id, created: true };
}

export async function updateDailyLogTaskRelations(
  env: DailyLogTaskRelationEnv,
  targetDate = getJstDateString(),
): Promise<DailyLogTaskRelationResult> {
  const range = buildYesterdayRange(targetDate);
  const doneStatus = env.TASK_STATUS_DONE || DEFAULT_DONE_STATUS;
  const dropStatus =
    env.TASK_STATUS_DROPPED || env.TASK_STATUS_DROP_VALUE || DEFAULT_DROP_STATUS;

  console.log(
    `DailyLog relations: target=${targetDate} range=${range.startJst}..${range.endJst}`,
  );

  const [doneTaskIds, dropTaskIds] = await Promise.all([
    fetchTaskIdsByStatus(env, doneStatus, "Done date", range),
    fetchTaskIdsByStatus(env, dropStatus, "Drop date", range),
  ]);

  console.log(
    `DailyLog relations: done=${doneTaskIds.length}, drop=${dropTaskIds.length}`,
  );

  const { pageId, created } = await findOrCreateDailyLogPage(env, targetDate);
  const updateResponse = await notionFetch(env, `pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify({
      properties: {
        "Done Tasks": createRelationProperty(doneTaskIds),
        "Drop Tasks": createRelationProperty(dropTaskIds),
      },
    }),
  });

  if (!updateResponse.ok) {
    throw new Error(await formatNotionError(updateResponse));
  }

  console.log(
    `DailyLog relations updated: page=${pageId} created=${created} done=${doneTaskIds.length} drop=${dropTaskIds.length}`,
  );

  return {
    target_date: targetDate,
    range: {
      start_jst: range.startJst,
      end_jst: range.endJst,
    },
    daily_log_page_id: pageId,
    created,
    done_count: doneTaskIds.length,
    drop_count: dropTaskIds.length,
  };
}
