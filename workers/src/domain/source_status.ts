export function mergeIngestSourceStatus(dataJson: string | undefined, previousJson: string): string | null {
  if (!dataJson) return null;
  let raw: any;
  try { raw = JSON.parse(dataJson)?.raw; } catch { return null; }
  const health = raw?.health;
  if (!health || typeof health.status !== "string") return null;
  let previous: Record<string, unknown> = {};
  try { previous = JSON.parse(previousJson || "{}"); } catch { /* replace malformed status only */ }
  const available: string[] = Array.isArray(health.available_fields) ? health.available_fields : [];
  const failed = health.status === "failed";
  const missing = health.status === "no_data";
  const statusFor = (prefixes: string[]) => failed ? "query_failed" : missing ? "missing"
    : available.some((name) => prefixes.some((prefix) => name.startsWith(prefix))) ? "observed" : "missing";
  const state = (status: string) => ({ status, source: "health_ingest", reason: String(health.error_code || health.status) });
  return JSON.stringify({ ...previous,
    health: state(failed ? "query_failed" : missing ? "missing" : "partial"),
    sleep: state(statusFor(["sleep", "in_bed", "rem", "deep"])),
    nutrition: state(statusFor(["kcal", "protein", "fat", "carb"])),
  });
}
