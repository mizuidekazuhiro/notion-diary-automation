// Notion value codecs only. Chunk limits and missing/zero semantics are preserved.
export const BODY_CHUNK_LENGTH = 1800;

export const NOTES_RICH_TEXT_LIMIT = 2000;

export function getPageTitleFromProperty(
  page: Record<string, any>,
  propertyName: string,
): string {
  const titleProp = page.properties?.[propertyName]?.title;
  if (!Array.isArray(titleProp)) {
    return "";
  }
  return titleProp.map((item: { plain_text: string }) => item.plain_text).join("");
}

export function createTitleProperty(title: string) {
  return {
    title: [
      {
        text: { content: title },
      },
    ],
  };
}

export function createRichTextProperty(content: string) {
  const chunks = splitIntoChunks(content, BODY_CHUNK_LENGTH);
  return {
    rich_text: chunks.map((chunk) => ({
      text: { content: chunk },
    })),
  };
}

export function createRichTextPropertyWithLimit(content: string, limit = NOTES_RICH_TEXT_LIMIT) {
  const items = splitRichText(content, limit);
  return {
    rich_text: items,
  };
}

export function createDateProperty(date: string) {
  return {
    date: date ? { start: date } : null,
  };
}

export function createSelectProperty(name: string) {
  return {
    select: name ? { name } : null,
  };
}

export function createNumberProperty(value?: number | null) {
  return {
    number: typeof value === "number" ? value : null,
  };
}

export function createFilesProperty(files: Array<Record<string, any>>) {
  return {
    files,
  };
}

export function createRelationProperty(ids: string[]) {
  return {
    relation: ids.map((id) => ({ id })),
  };
}

export function createCheckboxProperty(value: boolean) {
  return {
    checkbox: value,
  };
}

export function splitIntoChunks(content: string, maxLength: number): string[] {
  if (!content) {
    return [];
  }
  const chunks: string[] = [];
  for (let start = 0; start < content.length; start += maxLength) {
    chunks.push(content.slice(start, start + maxLength));
  }
  return chunks;
}

export function splitRichText(text: string, limit = NOTES_RICH_TEXT_LIMIT) {
  return splitIntoChunks(text, limit).map((chunk) => ({
    type: "text",
    text: { content: chunk },
  }));
}

export function getPlainTextFromRichText(richTextOrProperty: any): string {
  if (!richTextOrProperty) {
    return "";
  }
  const richText = Array.isArray(richTextOrProperty)
    ? richTextOrProperty
    : richTextOrProperty.rich_text;
  if (!Array.isArray(richText)) {
    return "";
  }
  return richText
    .map((item: { plain_text?: string }) => item.plain_text ?? "")
    .join("");
}

export function getStringFromProperty(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const richText = getPlainTextFromRichText(property).trim();
  if (richText) {
    return richText;
  }
  const titleText = getPlainTextFromTitle(property).trim();
  if (titleText) {
    return titleText;
  }
  const selectName =
    typeof property.select?.name === "string" ? property.select.name.trim() : "";
  return selectName;
}

export function getDateTimeFromProperty(property: Record<string, any> | undefined): string {
  const start =
    typeof property?.date?.start === "string" ? property.date.start.trim() : "";
  return start;
}

export function getIsoStringFromProperty(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const dateValue = getDateTimeFromProperty(property);
  if (dateValue) {
    return dateValue;
  }
  const directText = getStringFromProperty(property).trim();
  if (directText) {
    return directText;
  }
  const formula = property.formula;
  if (formula) {
    if (typeof formula.string === "string" && formula.string.trim()) {
      return formula.string.trim();
    }
    if (typeof formula.number === "number") {
      return String(formula.number);
    }
    if (typeof formula.boolean === "boolean") {
      return formula.boolean ? "true" : "false";
    }
    if (typeof formula.date?.start === "string" && formula.date.start.trim()) {
      return formula.date.start.trim();
    }
  }
  return "";
}

export function getPlainTextFromTitle(property: Record<string, any> | undefined): string {
  if (!property) {
    return "";
  }
  const title = property.title;
  if (!Array.isArray(title)) {
    return "";
  }
  return title
    .map((item: { plain_text?: string }) => item.plain_text ?? "")
    .join("");
}

export function getNumberFromProperty(
  property: Record<string, any> | undefined,
): number | null {
  if (!property || typeof property.number !== "number") {
    return null;
  }
  return property.number;
}

export type NotionFilePropertyValue =
  | { name: string; type: "external"; external: { url: string } }
  | { name: string; type: "file"; file: { url: string } };

export function normalizeFilesFromProperty(
  property: Record<string, any> | undefined,
): NotionFilePropertyValue[] {
  if (!property || !Array.isArray(property.files)) {
    return [];
  }
  return property.files
    .map((file: Record<string, any>) => {
      const name = typeof file.name === "string" ? file.name : "file";
      if (file.type === "external" && file.external?.url) {
        return { name, type: "external", external: { url: file.external.url } };
      }
      if (file.type === "file" && file.file?.url) {
        return { name, type: "file", file: { url: file.file.url } };
      }
      return null;
    })
    .filter((item): item is NotionFilePropertyValue => Boolean(item));
}

export function getFileUrlsFromProperty(
  property: Record<string, any> | undefined,
): string[] {
  return normalizeFilesFromProperty(property)
    .map((file: Record<string, any>) => {
      if (file.type === "external" && file.external?.url) {
        return String(file.external.url);
      }
      if (file.type === "file" && file.file?.url) {
        return String(file.file.url);
      }
      return null;
    })
    .filter((item): item is string => Boolean(item));
}

export function getRelationIdsFromProperty(
  property: Record<string, any> | undefined,
): string[] {
  if (!property || !Array.isArray(property.relation)) {
    return [];
  }
  return property.relation
    .map((item: { id?: string }) => item.id)
    .filter((id): id is string => Boolean(id));
}

export function buildNotionPageUrl(pageId: string): string {
  return `https://www.notion.so/${pageId.replace(/-/g, "")}`;
}

export function getNumberLikeFromProperty(property: Record<string, any> | undefined): number | null {
  const num = getNumberFromProperty(property);
  if (typeof num === "number") {
    return num;
  }
  const text = getStringFromProperty(property).trim();
  if (!text) {
    return null;
  }
  const parsed = Number.parseFloat(text);
  return Number.isFinite(parsed) ? parsed : null;
}

export function getDateStartFromProperty(property: Record<string, any> | undefined): string {
  const start = property?.date?.start;
  return typeof start === "string" ? start : "";
}

export function getCheckboxFromProperty(property: Record<string, any> | undefined): boolean | null {
  if (!property || typeof property.checkbox !== "boolean") {
    return null;
  }
  return property.checkbox;
}
