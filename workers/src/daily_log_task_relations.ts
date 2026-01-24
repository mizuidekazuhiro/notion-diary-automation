import { getJstDateString, getJstRangeForTargetDate } from "./date_utils";
import {
  getNotionErrorDetails,
  NotionApiError,
  notionFetch,
  queryDatabaseAll,
} from "./notion_client";
import { getTaskPropertyNames, TaskPropertyNameEnv } from "./task_property_names";
import { TITLE_PROPERTIES } from "./title_properties";

export type DailyLogTaskRelationEnv = {
  NOTION_TOKEN: string;
  TASK_DB_ID: string;
  DAILY_LOG_DB_ID: string;
  TASK_STATUS_DONE?: string;
  TASK_STATUS_DROPPED?: string;
  TASK_STATUS_DROP_VALUE?: string;
  TASK_STATUS_PROPERTY_NAME?: string;
  TASK_DONE_DATE_PROPERTY_NAME?: string;
  TASK_DROP_DATE_PROPERTY_NAME?: string;
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
const DEFAULT_DROP_STATUS = "Drop";

function logNotionQueryPayload(context: string, filter: Record<string, any>) {
  console.log(
    `Notion query payload (${context}): ${JSON.stringify({
      page_size: 100,
      database_id: "***",
      filter,
    })}`,
  );
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

function getTaskTitle(page: Record<string, any>): string | null {
  const title = page.properties?.[TITLE_PROPERTIES.tasks]?.title ?? [];
  if (!Array.isArray(title) || title.length === 0) {
    return null;
  }
  return title.map((item: Record<string, any>) => item.plain_text ?? "").join("");
}

type JstRange = {
  start_jst_iso: string;
  end_jst_iso: string;
};

type TaskRangeItem = {
  id: string;
  title: string | null;
  dateRaw: string | null;
  inRange: boolean;
};

function isDateTimeInRange(dateTime: string, range: JstRange): boolean {
  const dateValue = Date.parse(dateTime);
  const startValue = Date.parse(range.start_jst_iso);
  const endValue = Date.parse(range.end_jst_iso);
  if ([dateValue, startValue, endValue].some(Number.isNaN)) {
    return false;
  }
  return dateValue >= startValue && dateValue < endValue;
}

async function fetchTaskIdsByStatus(
  env: DailyLogTaskRelationEnv & TaskPropertyNameEnv,
  status: string,
  dateProperty: string,
  range: JstRange,
): Promise<TaskRangeItem[]> {
  const { statusPropertyName } = getTaskPropertyNames(env);
  const filter = {
    and: [
      { property: statusPropertyName, select: { equals: status } },
      { property: dateProperty, date: { is_not_empty: true } },
      { property: dateProperty, date: { on_or_after: range.start_jst_iso } },
      { property: dateProperty, date: { before: range.end_jst_iso } },
    ],
  };
  logNotionQueryPayload(`tasks/${status}`, filter);
  const pages = await queryDatabaseAll(env, env.TASK_DB_ID, filter);

  return pages
    .map((page: Record<string, any>) => {
      const dateRaw = page.properties?.[dateProperty]?.date?.start ?? null;
      return {
        id: page.id,
        title: getTaskTitle(page),
        dateRaw,
        inRange: dateRaw ? isDateTimeInRange(dateRaw, range) : false,
      };
    })
    .filter((item) => item.dateRaw);
}

async function findOrCreateDailyLogPage(
  env: DailyLogTaskRelationEnv,
  targetDate: string,
): Promise<{ pageId: string; created: boolean }> {
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
    const details = await getNotionErrorDetails(queryResponse);
    throw new NotionApiError(details);
  }

  const queryData = await queryResponse.json();
  const existingPage = (queryData.results ?? [])[0];
  if (existingPage) {
    return { pageId: existingPage.id, created: false };
  }

  const title = `${targetDate} Daily Log`;
  const createResponse = await notionFetch(env, "/pages", {
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
    const details = await getNotionErrorDetails(createResponse);
    throw new NotionApiError(details);
  }

  const createdPage = await createResponse.json();
  return { pageId: createdPage.id, created: true };
}

export async function updateDailyLogTaskRelations(
  env: DailyLogTaskRelationEnv,
  targetDate = getJstDateString(),
): Promise<DailyLogTaskRelationResult> {
  const range = getJstRangeForTargetDate(targetDate);
  const { doneDatePropertyName, dropDatePropertyName } = getTaskPropertyNames(env);
  const doneStatus = env.TASK_STATUS_DONE || DEFAULT_DONE_STATUS;
  const dropStatus =
    env.TASK_STATUS_DROPPED || env.TASK_STATUS_DROP_VALUE || DEFAULT_DROP_STATUS;

  console.log(
    `DailyLog relations: target_date=${targetDate}(JST) start_jst_iso=${range.start_jst_iso} end_jst_iso=${range.end_jst_iso}`,
  );

  const [doneTasks, dropTasks] = await Promise.all([
    fetchTaskIdsByStatus(env, doneStatus, doneDatePropertyName, range),
    fetchTaskIdsByStatus(env, dropStatus, dropDatePropertyName, range),
  ]);
  const doneTaskIds = doneTasks.map((item) => item.id);
  const dropTaskIds = dropTasks.map((item) => item.id);

  console.log(
    `DailyLog relations: done=${doneTaskIds.length}, drop=${dropTaskIds.length}`,
  );
  for (const item of doneTasks.slice(0, 3)) {
    console.log(
      `DailyLog relations: done_sample title="${item.title}" done_date=${item.dateRaw} in_range=${item.inRange}`,
    );
  }
  for (const item of dropTasks.slice(0, 3)) {
    console.log(
      `DailyLog relations: drop_sample title="${item.title}" drop_date=${item.dateRaw} in_range=${item.inRange}`,
    );
  }

  const { pageId, created } = await findOrCreateDailyLogPage(env, targetDate);
  const updateResponse = await notionFetch(env, `/pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify({
      properties: {
        "Done Tasks": createRelationProperty(doneTaskIds),
        "Drop Tasks": createRelationProperty(dropTaskIds),
      },
    }),
  });

  if (!updateResponse.ok) {
    const details = await getNotionErrorDetails(updateResponse);
    throw new NotionApiError(details);
  }

  console.log(
    `DailyLog relations updated: page=${pageId} created=${created} done=${doneTaskIds.length} drop=${dropTaskIds.length}`,
  );

  return {
    target_date: targetDate,
    range: {
      start_jst: range.start_jst_iso,
      end_jst: range.end_jst_iso,
    },
    daily_log_page_id: pageId,
    created,
    done_count: doneTaskIds.length,
    drop_count: dropTaskIds.length,
  };
}
