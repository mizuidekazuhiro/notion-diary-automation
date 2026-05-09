import { getJstYesterdayString, isValidDateString } from "../utils/date_utils";

export type TargetDateResolution =
  | { ok: true; targetDate: string }
  | { ok: false; reason: "invalid target_date format" };

export function resolveIngestTargetDate(payload: Record<string, any>): TargetDateResolution {
  const rawTargetDate =
    typeof payload.target_date === "string" ? payload.target_date.trim() : "";
  const rawDate = typeof payload.date === "string" ? payload.date.trim() : "";
  const targetDate = rawTargetDate || rawDate || getJstYesterdayString();

  if (!isValidDateString(targetDate)) {
    return { ok: false, reason: "invalid target_date format" };
  }

  return { ok: true, targetDate };
}

export function collectMealPhotosFromHealthPages(
  pages: Array<Record<string, any>>,
  healthMealPhotoPropertyName: string,
  normalizeFilesFromProperty: (property: Record<string, any> | undefined) => Array<Record<string, any>>,
): Array<Record<string, any>> {
  const photos: Array<Record<string, any>> = [];
  const seen = new Set<string>();

  for (const page of pages) {
    const pagePhotos = normalizeFilesFromProperty(
      page?.properties?.[healthMealPhotoPropertyName],
    );
    for (const photo of pagePhotos) {
      const key =
        photo.type === "external"
          ? `external:${photo.external?.url ?? ""}`
          : photo.type === "file"
            ? `file:${photo.file?.url ?? ""}`
            : JSON.stringify(photo);
      if (!key || seen.has(key)) {
        continue;
      }
      seen.add(key);
      photos.push(photo);
    }
  }

  return photos;
}

export function buildPhotoOnlyUpdateProperties(
  dailyLogMealPhotoPropertyName: string,
  photosPropertyValue: Record<string, any>,
): Record<string, any> {
  return {
    [dailyLogMealPhotoPropertyName]: photosPropertyValue,
  };
}

export function mergeMealPhotos(
  existingPhotos: Array<Record<string, any>>,
  incomingPhotos: Array<Record<string, any>>,
): Array<Record<string, any>> {
  const merged: Array<Record<string, any>> = [];
  const seen = new Set<string>();
  for (const photo of [...existingPhotos, ...incomingPhotos]) {
    const key =
      photo.type === "external"
        ? `external:${photo.external?.url ?? ""}`
        : photo.type === "file"
          ? `file:${photo.file?.url ?? ""}`
          : JSON.stringify(photo);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    merged.push(photo);
  }
  return merged;
}

export const mergeNotionFilesDedup = mergeMealPhotos;

export function buildMealPhotoUpdateProperties(
  dailyLogMealPhotoPropertyName: string,
  existingPhotos: Array<Record<string, any>>,
  incomingPhotos: Array<Record<string, any>>,
): Record<string, any> {
  if (!incomingPhotos.length) {
    return {};
  }
  const merged = mergeMealPhotos(existingPhotos, incomingPhotos);
  if (!merged.length) {
    return {};
  }
  return buildPhotoOnlyUpdateProperties(dailyLogMealPhotoPropertyName, {
    files: merged,
  });
}
