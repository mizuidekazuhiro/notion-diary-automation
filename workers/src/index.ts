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
import {
  handleStudyAnkiDaily,
  handleStudyReconcile,
  handleStudySession,
} from "./application/study_session";

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
  CANONICAL_DAY_BOUNDARY_HOUR?: string;
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

const MOOD_OPTIONS = ["â˜…", "â˜…â˜…", "â˜…â˜…â˜…", "â˜…â˜…â˜…â˜…", "â˜…â˜…â˜…â˜…â˜…"] as const;

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
  0: "æ™´ã‚Œ",
  1: "æ™´ã‚Œ",
  2: "æ›‡ã‚Š",
  3: "æ›‡ã‚Š",
  45: "éœ§",
  48: "éœ§",
  51: "é›¨",
  53: "é›¨",
  55: "é›¨",
  61: "é›¨",
  63: "é›¨",
  65: "é›¨",
  71: "é›ª",
  73: "é›ª",
  75: "é›ª",
  80: "é›¨",
  81: "é›¨",
  82: "é›¨",
  95: "é›·é›¨",
};
const WEATHER_SELECT_LABELS = ["æ™´ã‚Œ", "æ›‡ã‚Š", "é›¨", "é›ª", "é›·é›¨", "éœ§"] as const;

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
let mailLinkKeyPromise: Promise<CryptoKey>ç¾=ÚÚ$z{-®éÜj×F–'”æ÷F–f–6F–öåfW'6–öâÀ¢vVF†W#¢vVF†W$ÆVv7•FW‡BÀ¢vVF†W%÷7VÖÖ'“¢vVF†W%7VÖÖ'’À¢vVF†W%öÆö6F–öã¢vVF†W$Æö6F–öâÀ¢vVF†W%÷FV×öÖ…ö3¢vVF†W%FV×Ö„2À¢vVF†W%÷FV×öÖ–åö3¢vVF†W%FV×Ö–ä2À¢vVF†W%÷&V6—÷&ö&&–Æ—G•öÖƒ¢vVF†W%&V6—&ö&&–Æ—G”Ö‚À¢vVF†W%ö6öFS¢vVF†W$6öFRÀ¢vVF†W%÷&WG&–WfVEöC¢vVF†W%&WG&–WfVDBÀ¢vVF†W%ö–çWEö†6ƒ¢vVF†W$–çWD†6‚À¢vVF†W%övVæW&FVEöC¢vVF†W$vVæW&FVDBÀ¢Ò’À¢²†VFW'3¢§6öä†VFW'2ÒÀ¢“°§Ğ ¦gVæ7F–öâö†—7F÷'”çVÖ&W"‡&÷W'F–W3¢&V6÷&CÇ7G&–ærÂç“âÂ¶W“¢7G&–ær“¢çVÖ&W"ÂçVÆÂ°¢6öç7BfÇVRÒ&÷W'F–W5¶¶W•ÓòæçVÖ&W#°¢&WGW&âG—VöbfÇVRÓÓÒ&çVÖ&W""òfÇVR¢çVÆÃ°§Ğ ¦gVæ7F–öâö†—7F÷'”&ööÂ‡&÷W'F–W3¢&V6÷&CÇ7G&–ærÂç“âÂ¶W“¢7G&–ær“¢&ööÆVâÂçVÆÂ°¢6öç7BfÇVRÒ&÷W'F–W5¶¶W•Óòæ6†V6¶&÷ƒ°¢&WGW&âG—VöbfÇVRÓÓÒ&&ööÆVâ"òfÇVR¢çVÆÃ°§Ğ ¦7–æ2gVæ7F–öâ†æFÆTF–Ç”Æöt†—7F÷'’‡&WVW7C¢&WVW7BÂVçc¢Vçb“¢&öÖ—6SÅ&W7öç6Sâ°¢–b‡&WVW7BæÖWF†öBÓÒ$tUB"’&WGW&âÖWF†öDæ÷DÆÆ÷vVB‚“°¢6öç7BWF„W'&÷"Òv—B&WV—&T&V&W%Fö¶Vâ‡&WVW7BÂVçb“°¢–b†WF„W'&÷"’&WGW&âWF„W'&÷#° ¢6öç7BW&ÂÒæWrU$Â‡&WVW7BçW&Â“°¢6öç7BVæBÒ‡W&Âç6V&6…&×2ævWB‚&VæB"’ÇÂ""’çG&–Ò‚“°¢6öç7B7F'BÒ‡W&Âç6V&6…&×2ævWB‚'7F'B"’ÇÂ""’çG&–Ò‚“°¢6öç7BF—5&rÒ‡W&Âç6V&6…&×2ævWB‚&F—2"’ÇÂ""’çG&–Ò‚“°¢–b‚VæBÇÂ—5fÆ–DFFU7G&–ær†VæB’’&WGW&â&E&WVW7B‚&–çfÆ–B÷"Ö—76–ærVæB"“°¢ÆWB&W6öÇfVE7F'BÒ7F'C°¢–b‚&W6öÇfVE7F'B’°¢6öç7BF—2ÒçVÖ&W"ç'6T–çB†F—5&rÇÂ""Â“°¢–b‚çVÖ&W"æ—4f–æ—FR†F—2’ÇÂF—2ÃÒ’&WGW&â&E&WVW7B‚'7F'B÷"fÆ–BF—2—2&WV—&VB"“°¢&W6öÇfVE7F'BÒFDF—5Fô§7DFFR†VæBÂÒ†F—2Ò’“°¢Ğ¢–b‚—5fÆ–DFFU7G&–ær‡&W6öÇfVE7F'B’’&WGW&â&E&WVW7B‚&–çfÆ–B7F'B"“° ¢6öç7BvW2Òv—BVW'”FF&6TÆÂ†VçbÂVçbäD”Å•ôÄôuôD%ô”BÂ°¢æC¢°¢²&÷W'G“¢%F&vWBFFR"ÂFFS¢²öåö÷%ögFW#¢&W6öÇfVE7F'BÒÒÀ¢²&÷W'G“¢%F&vWBFFR"ÂFFS¢²öåö÷%ö&Vf÷&S¢VæBÒÒÀ¢ÒÀ¢Ò“°¢6öç7B†VÇF„æÖW2ÒvWDF–Ç”Æöt†VÇF…&÷W'G”æÖW2†Vçb“°¢6öç7BW‡Vç6TæÖW2ÒvWDF–Ç”ÆötW‡Vç6W5&÷W'G”æÖW2†Vçb“° ¢6öç7B—FV×2ÒvW0¢æÖ‚‡vS¢ç’’Óâ°¢6öç7BÒvRç&÷W'F–W2óò·Ó°¢6öç7BF&vWDFFRÒvWDFFU7F'Dg&öÕ&÷W'G’‡²%F&vWBFFR%Ò“°¢–b‚F&vWDFFR’&WGW&âçVÆÃ°¢6öç7BÆö6F–öâÒ&W6öÇfTÆö6F–öå7VÖÖ'”f–VÆG2‡ÂVçb“°¢6öç7BFöæU&VÆF–öä–G2ÒvWE&VÆF–öä–G4g&öÕ&÷W'G’‡²$FöæRF6·2%Ò“°¢6öç7BG&÷&VÆF–öä–G2ÒvWE&VÆF–öä–G4g&öÕ&÷W'G’‡²$G&÷F6·2%Ò“°¢&WGW&â°¢F&vWEöFFS¢F&vWDFFRÀ¢FFS¢vWDFFU7F'Dg&öÕ&÷W'G’‡äFFR’À¢vUö–C¢vRæ–BÀ¢F—FÆS¢vWEÆ–åFW‡Dg&öÕF—FÆR‡µD•DÄUõ$õU%D”U2æF–Ç”ÆöuÒ’ÇÂ""À¢7F—f—G•÷7VÖÖ'“¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$7F—f—G’7VÖÖ'’%Ò’ÇÂçVÆÂÀ¢æ÷FW3¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡¶vWDF–Ç”Æötæ÷FW5&÷W'G”æÖR†Vçb•Ò’ÇÂçVÆÂÀ¢Æö6F–öå÷7VÖÖ'“¢Æö6F–öâæÆö6F–öå7VÖÖ'’À¢Æö6F–öå÷7VÖÖ'•÷6÷W&6S¢Æö6F–öâæÆö6F–öå7VÖÖ'•6÷W&6RÀ¢ÖVÅ÷7VÖÖ'“¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$ÖVÂ7VÖÖ'’%Ò’ÇÂçVÆÂÀ¢¶6Ã¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2æ¶6Â’À¢&÷FV–ã¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç&÷FV–â’À¢fC¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2æfB’À¢6&#¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2æ6&"’À¢vV–v‡C¢ö†—7F÷'”çVÖ&W"‡Â%vV–v‡B"’À¢ÖööC¢äÖööCòç6VÆV7CòææÖRóòçVÆÂÀ¢FöæUö6÷VçC¢ö†—7F÷'”çVÖ&W"‡Â$FöæR6÷VçB"’óòFöæU&VÆF–öä–G2æÆVæwF‚À¢G&÷ö6÷VçC¢ö†—7F÷'”çVÖ&W"‡Â$G&÷6÷VçB"’óòG&÷&VÆF–öä–G2æÆVæwF‚À¢FöæU÷F6·3¢µÒÀ¢FöæU÷F6·5öFWF–Ã¢µÒÀ¢G&÷÷F6·3¢µÒÀ¢W‡Vç6W5÷F÷FÃ¢ö†—7F÷'”çVÖ&W"‡ÂW‡Vç6TæÖW2çF÷FÂ’À¢7GVG•öÖ–çWFW3¢ö†—7F÷'”çVÖ&W"‡Â%7GVG’Ö–çWFW2"’À¢7GVG•÷6W76–öç3¢ö†—7F÷'”çVÖ&W"‡Â%7GVG’6W76–öç2"’À¢7GVG•öÆ7E÷W6VEöC¢vWDFFUF–ÖTg&öÕ&÷W'G’‡²%7GVG’Æ7BW6VBB%Ò’À¢6ÆVW÷7F'C¢vWDFFUF–ÖTg&öÕ&÷W'G’‡¶†VÇF„æÖW2ç6ÆVW7F'EÒ’À¢6ÆVWöVæC¢vWDFFUF–ÖTg&öÕ&÷W'G’‡¶†VÇF„æÖW2ç6ÆVWVæEÒ’À¢6ÆVWöGW&F–öåöÖ–ã¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç6ÆVWGW&F–öäÖ–â’À¢6ÆVW÷66÷&S¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç6ÆVW66÷&R’À¢6ÆVW÷6÷W&6S¢vWE7G&–ætg&öÕ&÷W'G’‡¶†VÇF„æÖW2ç6ÆVW6÷W&6UÒ’À¢&VF–æW75÷7F'3¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç&VF–æW757F'2’À¢&VF–æW75ö‡'c¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç&VF–æW74‡'b’À¢&VF–æW75ö'Ó¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç&VF–æW74'Ò’À¢&6VÆ–æUö‡'c¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2æ&6VÆ–æT‡'b’À¢&6VÆ–æU÷v¶–æuö'Ó¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2æ&6VÆ–æUv¶–æt'Ò’À¢6ÆVWö†V'E÷&FS¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç6ÆVW†V'E&FR’À¢FVWöGW&F–öåöÖ–ã¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2æFVWGW&F–öäÖ–â’À¢&VÕöGW&F–öåöÖ–ã¢ö†—7F÷'”çVÖ&W"‡Â†VÇF„æÖW2ç&VÔGW&F–öäÖ–â’À¢æ÷FW5÷7G&W75öfÆs¢ö†—7F÷'”&ööÂ‡Â$æ÷FW27G&W72fÆr"’À¢æ÷FW5÷6ÆVWö—77VUöfÆs¢ö†—7F÷'”&ööÂ‡Â$æ÷FW26ÆVW—77VRfÆr"’À¢æ÷FW5öfF–wVUöfÆs¢ö†—7F÷'”&ööÂ‡Â$æ÷FW2fF–wVRfÆr"’À¢æ÷FW5÷6ö6–ÅöÆöEöfÆs¢ö†—7F÷'”&ööÂ‡Â$æ÷FW26ö6–ÂÆöBfÆr"’À¢æ÷FW5öÆ&VÅö–çWEö†6ƒ¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$æ÷FW2Æ&VÂ–çWB†6‚%Ò’ÇÂçVÆÂÀ¢æ÷FW5öfÆw5ö§6öã¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$æ÷FW2fÆw2¥4ôâ%Ò’ÇÂçVÆÂÀ¢æ÷FW5÷Fw5ö§6öã¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$æ÷FW2Fw2¥4ôâ%Ò’ÇÂçVÆÂÀ¢vVF†W%ö6öFS¢ö†—7F÷'”çVÖ&W"‡Â%vVF†W"6öFR"’À¢vVF†W%÷FV×öÖ…ö3¢ö†—7F÷'”çVÖ&W"‡Â%vVF†W"FV×Ö‚2"’À¢vVF†W%÷FV×öÖ–åö3¢ö†—7F÷'”çVÖ&W"‡Â%vVF†W"FV×Ö–â2"’À¢vVF†W%÷&V6—÷&ö&&–Æ—G•öÖƒ¢ö†—7F÷'”çVÖ&W"‡Â%vVF†W"&V6—&ö&&–Æ—G’Ö‚"’À¢vVF†W%ö–çWEö†6ƒ¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²%vVF†W"–çWB†6‚%Ò’ÇÂçVÆÂÀ¢e÷&—6µ÷66÷&S¢ö†—7F÷'”çVÖ&W"‡Â$b&—6²66÷&R"’À¢e÷&—6µ÷&V6öã¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$b&—6²&V6öâ%Ò’ÇÂçVÆÂÀ¢e÷&—6µö–çWEö†6ƒ¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$b&—6²–çWB†6‚%Ò’ÇÂçVÆÂÀ¢W‡Vç6Uöeö6÷VçC¢ö†—7F÷'”çVÖ&W"‡Â$W‡Vç6Rb6÷VçB"’À¢W‡Vç6Uöe÷F÷FÃ¢ö†—7F÷'”çVÖ&W"‡Â$W‡Vç6RbF÷FÂ"’À¢W‡Vç6UöeöÖW&6†çG3¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$W‡Vç6RbÖW&6†çG2%Ò’ÇÂçVÆÂÀ¢W‡Vç6Uöeö6FVv÷&–W3¢vWEÆ–åFW‡Dg&öÕ&–6…FW‡B‡²$W‡Vç6Rb6FVv÷&–W2%Ò’ÇÂçVÆÂÀ¢Ó°¢Ò¢æf–ÇFW"‚†—FVÓ¢ç’’Óâ—FVÒ¢ç6÷'B‚†¢ç’Â#¢ç’’Óâ†çF&vWEöFFRÂ"çF&vWEöFFRò¢çF&vWEöFFRâ"çF&vWEöFFRòÓ¢’“° ¢&WGW&âæWr&W7öç6R€¢¥4ôâç7G&–æv–g’‡²f÷VæC¢G'VRÂ7F'C¢&W6öÇfVE7F'BÂVæBÂ6÷VçC¢—FV×2æÆVæwF‚Â—FV×2Ò’À¢²†VFW'3¢§6öä†VFW'2ÒÀ¢“°§Ğ ¦7–æ2gVæ7F–öâ†æFÆUF6µ&öÖ÷FT6öæf—&Ò‡&WVW7C¢&WVW7B“¢&öÖ—6SÅ&W7öç6Sâ°¢6öç7BW&ÂÒæWrU$Â‡&WVW7BçW&Â“°¢6öç7BvT–BÒW&Âç6V&6…&×2ævWB‚&–B"“°¢–b‚vT–B’°¢&WGW&â&E&WVW7B‚&Ö—76–ær–B"“°¢Ğ ¢6öç7B‡FÖÂÒ ¢Æƒå&öÖ÷FRF6³Âöƒà¢ÇåF6²”C¢G·vT–GÓÂ÷à¢Æf÷&ÒÖWF†öCÒ'÷7B"7F–öãÒ"öW†V7WFR÷F6·2÷&öÖ÷FR#à¢Æ–çWBG—SÒ&†–FFVâ"æÖSÒ&–B"fÇVSÒ"G·vT–GÒ"óà¢Æ'WGFöâG—SÒ'7V&Ö—B#å&öÖ÷FRFòFóÂö'WGFöãà¢Âöf÷&Óà¢°¢&WGW&â7&VFT‡FÖÅvR‚$6öæf—&Ò&öÖ÷FR"Â‡FÖÂ“°§Ğ ¦7–æ2gVæ7F–öâ†æFÆUF6µ&öÖ÷FTW†V7WFR‡&WVW7C¢&WVW7BÂVçc¢Vçb“¢&öÖ—6SÅ&W7öç6Sâ°¢–b‡&WVW7BæÖWF†öBÓÒ%õ5B"’°¢&WGW&âÖWF†öDæ÷DÆÆ÷vVB‚'W6Rõ5BöW†V7WFR÷F6·2÷&öÖ÷FR"“°¢Ğ¢6öç7BWF„W'&÷"Òv—B&WV—&T&V&W%Fö¶Vâ‡&WVW7BÂVçb“°¢–b†WF„W'&÷"’°¢&WGW&âWF„W'&÷#°¢Ğ ¢6öç7B²Fõ7FGW2ÒÒvWEF6µ7FGW46öæf–r†Vçb“°¢v—BfÆ–FFUF6·4FF&6U66†VÖ†Vçb“° ¢6öç7Bf÷&ÔFFÒv—B&WVW7Bæf÷&ÔFF‚“°¢6öç7BvT–BÒf÷&ÔFFævWB‚&–B"“°¢–b‚vT–BÇÂG—VöbvT–BÓÒ'7G&–ær"’°¢&WGW&â&E&WVW7B‚&Ö—76–ær–B"“°¢Ğ ¢6öç7B§7DFFRÒvWD§7DFFU7G&–ær‚“°¢6öç7B&÷W'F–W2Ò°¢7FGW3¢7&VFU6VÆV7E&÷W'G’†Fõ7FGW2’À¢%6–æ6RFò#¢7&VFTFFU&÷W'G’†§7DFFR’À¢Ó° ¢6öç7B&W7öç6RÒv—Bæ÷F–öäfWF6‚†VçbÂ÷vW2òG·vT–GÖÂ°¢ÖWF†öC¢%D4‚"À¢&öG“¢¥4ôâç7G&–æv–g’‡²&÷W'F–W2Ò’À¢Ò“° ¢–b‚&W7öç6Ræö²’°¢&WGW&âæ÷F–öäW'&÷%&W7öç6R‡&W7öç6RÂ&†æFÆUF6µ&öÖ÷FTW†V7WFR"“°¢Ğ ¢&WGW&â7&VFT‡FÖÅvR‚%&öÖ÷FVB"Â#ÇåF6²&öÖ÷FVBFòFòãÂ÷â"“°§Ğ ¦7–æ2gVæ7F–öâ†æFÆTF–Ç”Æöt6öæf—&Ò‡&WVW7C¢&WVW7B“¢&öÖ—6SÅ&W7öç6Sâ°¢6öç7BW&ÂÒæWrU$Â‡&WVW7BçW&Â“°¢6öç7BF&vWDFFRÒW&Âç6V&6…&×2ævWB‚'F&vWEöFFR"’óò"#°¢6öç7BF—FÆRÒW&Âç6V&6…&×2ævWB‚'F—FÆR"’óò"#°¢6öç7B7VÖÖ'•FW‡BĞ¢W&Âç6V&6…&×2ævWB‚'7VÖÖ'•÷FW‡B"’óğ¢W&Âç6V&6…&×2ævWB‚&7F—f—G•÷7VÖÖ'’"’óğ¢"#°¢6öç7B7VÖÖ'”‡FÖÂÒW&Âç6V&6…&×2ævWB‚'7VÖÖ'•ö‡FÖÂ"’óò"#°¢6öç7BÖ–Ä–BÒW&Âç6V&6…&×2ævWB‚&Ö–Åö–B"’óò"#°¢6öç7B6÷W&6RÒW&Âç6V&6…&×2ævWB‚'6÷W&6R"’óò&WFöÖF–öâ#° ¢–b‚F&vWDFFRÇÂF—FÆRÇÂ7VÖÖ'•FW‡BÇÂÖ–Ä–B’°¢&WGW&â&E&WVW7B‚&Ö—76–ær&WV—&VBf–VÆG2"“°¢Ğ ¢6öç7B‡FÖÂÒ ¢ÆƒäF–Ç’ÆörW6W'CÂöƒà¢ÇåF&vWBFFS¢G·F&vWDFFWÓÂ÷à¢ÇåF—FÆS¢G·F—FÆWÓÂ÷à¢Çå6÷W&6S¢G·6÷W&6WÓÂ÷à¢Ç&SâG·7VÖÖ'•FW‡GÓÂ÷&Sà¢Æf÷&ÒÖWF†öCÒ'÷7B"7F–öãÒ"öW†V7WFRö’öF–Ç•öÆör÷W6W'B#à¢Æ–çWBG—SÒ&†–FFVâ"æÖSÒ'F&vWEöFFR"fÇVSÒ"G·F&vWDFFWÒ"óà¢Æ–çWBG—SÒ&†–FFVâ"æÖSÒ'F—FÆR"fÇVSÒ"G·F—FÆWÒ"óà¢Æ–çWBG—SÒ&†–FFVâ"æÖSÒ'7VÖÖ'•÷FW‡B"fÇVSÒ"G·7VÖÖ'•FW‡GÒ"óà¢Æ–çWBG—SÒ&†–FFVâ"æÖSÒ'7VÖÖ'•ö‡FÖÂ"fÇVSÒ"G·7VÖÖ'”‡FÖÇÒ"óà¢Æ–çWBG—SÒ&†–FFVâ"æÖSÒ&Ö–Åö–B"fÇVSÒ"G¶Ö–Ä–GÒ"óà¢Æ–çWBG—SÒ&†–FFVâ"æÖSÒ'6÷W&6R"fÇVSÒ"G·6÷W&6WÒ"óà¢Æ'WGFöâG—SÒ'7V&Ö—B#äW†V7WFRW6W'CÂö'WGFöãà¢Âöf÷&Óà¢° ¢&WGW&â7&VFT‡FÖÅvR‚$6öæf—&ÒF–Ç’Æör"Â‡FÖÂ“°§Ğ ¦7–æ2gVæ7F–öâ†æFÆTF–Ç”ÆötW†V7WFR‡&WVW7C¢&WVW7BÂVçc¢Vçb“¢&öÖ—6SÅ&W7öç6Sâ°¢–b‡&WVW7BæÖWF†öBÓÒ%õ5B"’°¢&WGW&âÖWF†öDæ÷DÆÆ÷vVB‚'W6Rõ5BöW†V7WFRö’öF–Ç•öÆör÷W6W'B"“°¢Ğ¢6öç7B6öçFVçEG—RÒ&WVW7Bæ†VFW'2ævWB‚&6öçFVçB×G—R"’óò"#°¢ÆWB–ÆöC¢&V6÷&CÇ7G&–ærÂ7G&–æsâÒ·Ó° ¢–b†6öçFVçEG—Ræ–æ6ÇVFW2‚&Æ–6F–öâö§6öâ"’’°¢6öç7B'6VBÒv—B'6T§6öä&öG’‡&WVW7B“°¢–b‚'6VB’°¢&WGW&â&E&WVW7B‚&–çfÆ–B§6öâ&öG’"“°¢Ğ¢–ÆöBÒ'6VB2&V6÷&CÇ7G&–ærÂ7G&–æsã°¢ÒVÇ6R°¢6öç7Bf÷&ÔFFÒv—B&WVW7Bæf÷&ÔFF‚“°¢f÷&ÔFFæf÷$V6‚‚‡fÇVRÂ¶W’’Óâ°¢–b‡G—VöbfÇVRÓÓÒ'7G&–ær"’°¢–ÆöE¶¶W•ÒÒfÇVS°¢Ğ¢Ò“°¢Ğ ¢6öç7B&÷‡”†VFW'2ÒæWr†VFW'2‡&WVW7Bæ†VFW'2“°¢&÷‡”†VFW'2ç6WB‚&6öçFVçB×G—R"Â&Æ–6F–öâö§6öã²6†'6WC×WFbÓ‚"“° ¢6öç7B&÷‡•&WVW7BÒæWr&WVW7B‡&WVW7BçW&ÂÂ°¢ÖWF†öC¢%õ5B"À¢†VFW'3¢&÷‡”†VFW'2À¢&öG“¢¥4ôâç7G&–æv–g’‡–ÆöB’À¢Ò“° ¢&WGW&â†æFÆTF–Ç”ÆöuW6W'B‡&÷‡•&WVW7BÂVçb“°§Ğ ¦W‡÷'BFVfVÇB°¢7–æ2fWF6‚‡&WVW7C¢&WVW7BÂVçc¢Vçb“¢&öÖ—6SÅ&W7öç6Sâ°¢6öç7BW&ÂÒæWrU$Â‡&WVW7BçW&Â“°¢6öç7BF‚Òæ÷&ÖÆ—¦UF‚‡W&ÂçF†æÖR“° ¢G'’°¢6öç7B&÷WFVBÒv—BF—7F6…&÷WFR‡F‚Â°¢µ$õUDU2ä”ä$õ…Ó¢‚’Óâ†æFÆT–æ&÷‚‡&WVW7BÂVçb’À¢µ$õUDU2åD4µ5Ó¢‚’Óâ†æFÆUF6·2‡&WVW7BÂVçb’À¢µ$õUDU2åD4µ5ô4Äõ4TEÓ¢‚’Óâ†æFÆUF6·46Æ÷6VB‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuõ$TEÓ¢‚’Óâ†æFÆTF–Ç”Æöu&VB‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuô„•5Dõ%•Ó¢‚’Óâ†æFÆTF–Ç”Æöt†—7F÷'’‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuõU4U%EÓ¢‚’Óà¢&öÖ—6Rç&W6öÇfR€¢æWr&W7öç6R€¢¥4ôâç7G&–æv–g’‡°¢W'&÷#¢'W6RöW†V7WFRö’öF–Ç•öÆör÷W6W'Bf÷"WFFW2"À¢Ò’À¢²7FGW3¢CRÂ†VFW'3¢§6öä†VFW'2ÒÀ¢’À¢’À¢µ$õUDU2äD”Å•ôÄôuô4ôäd•$ÕõU4U%EÓ¢‚’Óà¢&WVW7BæÖWF†öBÓÓÒ$tUB ¢ò†æFÆTF–Ç”Æöt6öæf—&Ò‡&WVW7B¢¢&öÖ—6Rç&W6öÇfR†ÖWF†öDæ÷DÆÆ÷vVB‚'W6RtUBö6öæf—&ÒöF–Ç•öÆör÷W6W'B"’’À¢µ$õUDU2äD”Å•ôÄôuôU„T5UDUõU4U%EÓ¢‚’Óâ†æFÆTF–Ç”ÆötW†V7WFR‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuô”ätU5Eô„TÅD…Ó¢‚’Óâ†æFÆTF–Ç”Æöt†VÇF„–ævW7B‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuô”ätU5Eõ„õDõ5Ó¢‚’Óâ†æFÆTF–Ç”Æöu†÷F÷4–ævW7B‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuô”ätU5EôD”Å•ôÄôuÓ¢‚’Óâ†æFÆTF–Ç”Æöt–ævW7B‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuô”ätU5EôU…Tå4U5Ó¢‚’Óâ†æFÆTF–Ç”ÆötW‡Vç6W4–ævW7B‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuô”ätU5EôÄô4D”ôåÓ¢‚’Óâ†æFÆTF–Ç”ÆötÆö6F–öä–ævW7B‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuôtTäU$DUôD”%•Ó¢‚’Óâ†æFÆTF–Ç”ÆötvVæW&FTF–'’‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuôÔ$µôD”%•ôäõD”d”TEÓ¢‚’Óà¢†æFÆTF–Ç”ÆötÖ&´F–'”æ÷F–f–VB‡&WVW7BÂVçb’À¢µ$õUDU2äÔôôEôäõDU5ô4ôäd•$ÕÓ¢‚’Óâ†æFÆTÖööDæ÷FW46öæf—&Ò‡&WVW7BÂVçb’À¢µ$õUDU2äÔôôEôäõDU5ôU„T5UDUÓ¢‚’Óâ†æFÆTÖööDæ÷FW4W†V7WFR‡&WVW7BÂVçb’À¢µ$õUDU2äÔôôEôäõDU5ô”ätU5EÓ¢‚’Óâ†æFÆTÖööDæ÷FW4–ævW7B‡&WVW7BÂVçb’À¢µ$õUDU2äD”Å•ôÄôuôTå5U$UÓ¢‚’Óâ†æFÆTF–Ç”ÆötVç7W&R‡&WVW7BÂVçb’À¢µ$õUDU2å5ETE•õ4U54”ôåÓ¢‚’Óâ†æFÆU7GVG•6W76–öâ‡&WVW7BÂVçb’À¢µ$õUDU2å5ETE•ôä´•ôD”Å•Ó¢‚’Óâ†æFÆU7GVG”æ¶”F–Ç’‡&WVW7BÂVçb’À¢µ$õUDU2å5ETE•õ$T4ôä4”ÄUÓ¢‚’Óâ†æFÆU7GVG•&V6öæ6–ÆR‡&WVW7BÂVçb’À¢µ$õUDU2åD4µ5õ$ôÔõDUô4ôäd•$ÕÓ¢‚’Óà¢&WVW7BæÖWF†öBÓÓÒ$tUB ¢ò†æFÆUF6µ&öÖ÷FT6öæf—&Ò‡&WVW7B¢¢&öÖ—6Rç&W6öÇfR†ÖWF†öDæ÷DÆÆ÷vVB‚'W6RtUBö6öæf—&Ò÷F6·2÷&öÖ÷FR"’’À¢µ$õUDU2åD4µ5õ$ôÔõDUôU„T5UDUÓ¢‚’Óâ†æFÆUF6µ&öÖ÷FTW†V7WFR‡&WVW7BÂVçb’À¢µ$õUDU2ä„TÅD…Ó¢‚’Óâ&öÖ—6Rç&W6öÇfR††VÇF„6†V6²‚’’À¢Ò“° ¢–b‡&÷WFVB’°¢&WGW&â&÷WFVC°¢Ğ ¢&WGW&âæ÷Df÷VæB‚“°¢Ò6F6‚†W'&÷"’°¢–b†W'&÷"–ç7Fæ6Vöbæ÷F–öä”W'&÷"’°¢6öç7B&öG•6æ—WBĞ¢W'&÷"æ&öG’æÆVæwF‚âC ¢òG¶W'&÷"æ&öG’ç6Æ–6RƒÂC—Òâââ‡G'Væ6FVB– ¢¢W'&÷"æ&öG“°¢6öç7B&WVW7D–DÆörÒW'&÷"ç&WVW7D–Bò&WVW7Eö–CÒG¶W'&÷"ç&WVW7D–GÖ¢"#°¢6öç6öÆRæW'&÷"€¢æ÷F–öâ’W'&÷#¢7FGW3ÒG¶W'&÷"ç7FGW7ÒG·&WVW7D–DÆöwÒG¶W'&÷"æÖW76vWÖÀ¢“°¢6öç6öÆRæW'&÷"†æ÷F–öâ’&W7öç6R&öG“¢G¶&öG•6æ—WGÖ“°¢6öç7B7FGW2ÒW'&÷"ç7FGW2ãÒCòW'&÷"ç7FGW2¢S°¢&WGW&âæWr&W7öç6R€¢¥4ôâç7G&–æv–g’‡°¢W'&÷#¢&æ÷F–öåöW'&÷""À¢7FGW2À¢6öFS¢W'&÷"æ6öFRóòçVÆÂÀ¢ÖW76vS¢W'&÷"ææ÷F–öäÖW76vRóòçVÆÂÀ¢&WVW7Eö–C¢W'&÷"ç&WVW7D–BóòçVÆÂÀ¢&öG“¢W'&÷"æ&öG’À¢Ò’À¢²7FGW2Â†VFW'3¢§6öä†VFW'2ÒÀ¢“°¢Ğ ¢6öç6öÆRæW'&÷"‚%Væ†æFÆVBW'&÷"â"ÂW'&÷"“°¢6öç7BÖW76vRÒW'&÷"–ç7Fæ6VöbW'&÷"òW'&÷"æÖW76vR¢%Væ¶æ÷vâW'&÷"#°¢&WGW&âæWr&W7öç6R€¢¥4ôâç7G&–æv–g’‡²W'&÷#¢&–çFW&æÅöW'&÷""ÂÖW76vRÒ’À¢°¢7FGW3¢SÀ¢†VFW'3¢§6öä†VFW'2À¢ÒÀ¢“°¢Ğ¢ÒÀ§Ó° ¦W‡÷'B6öç7Bõ÷FW7EõòÒ°¢'V–ÆD†VÇF„–ævW7EVW'”&öG’À¢'V–ÆDF–Ç”Æöu&÷W'F–W2À¢W‡G&7DÖ–ÄÖWFFFg&öÕ&÷W'F–W2À¢vWD†VÇF…&÷W'G”æÖW2À¢æ÷&ÖÆ—¦Tf–ÆW4g&öÕ&÷W'G’À¢vWDf–ÆUW&Ç4g&öÕ&÷W'G’À¢&W6öÇfTÆö6F–öå7VÖÖ'”f–VÆG2À¢'V–ÆDF–Ç”ÆöuW6W'E&÷W'F–W2À¢vWDÖVÅ†÷F÷4f–ÆW46÷VçBÀ¢'V–ÆDF–Ç”ÆöuW6W'DF–væ÷7F–72À¢6æ—F—¦TÖVÅ†÷F÷5F6…&÷W'F–W2À¢'6U7GVG•–ÆöBÀ¢Ç•7GVG•WFFU&÷W'F–W2À§Ó°