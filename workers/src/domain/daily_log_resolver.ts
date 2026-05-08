export type NotionPage = {
  id: string;
  created_time?: string;
  last_edited_time?: string;
  properties?: Record<string, any>;
  url?: string;
};

const TITLE_REGEX = /^Daily\s*Log\s*(?:｜|\|)?\s*(\d{4}-\d{2}-\d{2})$/i;

export function extractDailyLogDateFromTitle(title: string | null | undefined): string | null {
  if (!title) return null;
  const m = title.trim().match(TITLE_REGEX);
  return m ? m[1] : null;
}

export function getTitleFromPage(page: NotionPage): string {
  const p = page.properties ?? {};
  const titleProp = p["名前"] ?? p["Name"] ?? p["title"];
  const arr = titleProp?.title;
  if (Array.isArray(arr) && arr.length > 0) {
    return (arr[0]?.plain_text ?? arr[0]?.text?.content ?? "").trim();
  }
  return "";
}

function getDateProp(page: NotionPage, key: string): string | null {
  const start = page.properties?.[key]?.date?.start;
  return typeof start === "string" && start ? start.slice(0, 10) : null;
}

function hasText(page: NotionPage, key: string): boolean {
  const rich = page.properties?.[key]?.rich_text;
  return Array.isArray(rich) && rich.some((v: any) => (v?.plain_text ?? "").trim());
}
function hasFiles(page: NotionPage, key: string): boolean {
  const files = page.properties?.[key]?.files;
  return Array.isArray(files) && files.length > 0;
}
function hasSelect(page: NotionPage, key: string): boolean {
  return !!page.properties?.[key]?.select?.name;
}

export function chooseCanonicalDailyLogPage(pages: NotionPage[], targetDate: string): NotionPage | null {
  if (!pages.length) return null;
  const scored = pages.map((p) => {
    const date = getDateProp(p, "Date");
    const target = getDateProp(p, "Target Date");
    const hasCore = ["Diary", "Today advice", "Weather", "Mail ID"].some((k) => hasText(p, k));
    const hasAux = hasText(p, "Location summary (GPT)") || hasFiles(p, "Meal Photos") || hasSelect(p, "Mood") || hasText(p, "Notes");
    return {
      p,
      score: [date === targetDate && target === targetDate ? 1 : 0, hasCore ? 1 : 0, hasAux ? 1 : 0],
    };
  });
  scored.sort((a, b) => {
    for (let i = 0; i < a.score.length; i += 1) {
      if (a.score[i] !== b.score[i]) return b.score[i] - a.score[i];
    }
    const aEdit = new Date(a.p.last_edited_time ?? 0).getTime();
    const bEdit = new Date(b.p.last_edited_time ?? 0).getTime();
    if (aEdit !== bEdit) return bEdit - aEdit;
    const aCreated = new Date(a.p.created_time ?? 0).getTime();
    const bCreated = new Date(b.p.created_time ?? 0).getTime();
    return aCreated - bCreated;
  });
  return scored[0].p;
}

export function isPageMatchedByDateOrTitle(page: NotionPage, targetDate: string): boolean {
  const date = getDateProp(page, "Date");
  if (date === targetDate) return true;
  const target = getDateProp(page, "Target Date");
  if (target === targetDate) return true;
  return extractDailyLogDateFromTitle(getTitleFromPage(page)) === targetDate;
}
