export function getJstDateString(date = new Date()): string {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return formatter.format(date);
}

export function formatJstDateTime(dateString: string, time = "00:00:00"): string {
  const value = `${dateString}T${time}+09:00`;
  if (!value.endsWith("+09:00")) {
    throw new Error(`JST datetime must include +09:00 offset: ${value}`);
  }
  return value;
}

export function getJstDateStringFromDateTime(dateTime: string): string | null {
  if (!dateTime) {
    return null;
  }
  const date = new Date(dateTime);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return getJstDateString(date);
}

export function getJstYesterdayString(): string {
  const now = Date.now();
  return getJstDateString(new Date(now - 24 * 60 * 60 * 1000));
}

export function isValidDateString(dateString: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(dateString);
}

export function addDaysToJstDate(dateString: string, days: number): string {
  const date = new Date(`${dateString}T00:00:00+09:00`);
  date.setTime(date.getTime() + days * 24 * 60 * 60 * 1000);
  return getJstDateString(date);
}

export function parseDayBoundaryHour(
  raw: string | undefined,
  fallback = 5,
  settingName = "CANONICAL_DAY_BOUNDARY_HOUR",
): number {
  const value = raw === undefined || raw.trim() === "" ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < 0 || value > 23) {
    throw new Error(`${settingName} must be an integer from 0 to 23`);
  }
  return value;
}

export function resolveJstDayKey(dateTime: string | number | Date, boundaryHour = 5): string {
  const parsedBoundary = parseDayBoundaryHour(String(boundaryHour), 5);
  const epochMs = dateTime instanceof Date ? dateTime.getTime() :
    typeof dateTime === "number" ? dateTime : Date.parse(dateTime);
  if (!Number.isFinite(epochMs)) {
    throw new Error("dateTime must be a valid ISO-8601 datetime, epoch, or Date");
  }
  return getJstDateString(new Date(epochMs - parsedBoundary * 60 * 60 * 1000));
}

export function getJstRangeForTargetDate(targetDate: string): {
  start_jst_iso: string;
  end_jst_iso: string;
} {
  const start_jst_iso = `${targetDate}T00:00:00+09:00`;
  const nextDate = addDaysToJstDate(targetDate, 1);
  const end_jst_iso = `${nextDate}T00:00:00+09:00`;
  return { start_jst_iso, end_jst_iso };
}
