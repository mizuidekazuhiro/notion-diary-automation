export function getJstDateString(date = new Date()): string {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return formatter.format(date);
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
