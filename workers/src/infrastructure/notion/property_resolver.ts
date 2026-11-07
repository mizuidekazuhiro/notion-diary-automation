// Property-name/type resolution only. Schema fetching and caches stay in the Worker.
export type NotionPropertyType =
  | "title"
  | "rich_text"
  | "number"
  | "select"
  | "date"
  | "checkbox"
  | "relation"
  | "rollup"
  | "files";

export type ExpectedProperty = {
  name: string;
  type: NotionPropertyType;
};

export function normalizeNotionPropertyName(name: string): string {
  return name.trim().toLowerCase().replace(/[\s_-]+/g, "");
}

export function findExactOrNormalizedPropertyMatches(
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

export function findPropertyMatchesWithAliases(
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

export function resolvePropertyName(
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

export function resolveExactPropertyName(
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

export function hasPropertyType(
  properties: Record<string, any>,
  name: string,
  type: NotionPropertyType,
): boolean {
  const resolvedName = resolvePropertyName(properties, name, `hasPropertyType:${type}`);
  if (!resolvedName) {
    return false;
  }
  const schema = properties[resolvedName];
  return Boolean(schema && schema.type === type);
}

export function getResolvedProperty<T>(
  properties: Record<string, T>,
  name: string,
  context: string,
  aliases: string[] = [],
): T | undefined {
  const resolvedName = resolvePropertyName(properties as Record<string, any>, name, context, aliases);
  return resolvedName ? properties[resolvedName] : undefined;
}

export function resolvePropertyNameOrWarn(
  properties: Record<string, any>,
  desiredName: string,
  context: string,
  aliases: string[] = [],
): string | null {
  const resolvedName = resolvePropertyName(properties, desiredName, context, aliases);
  if (!resolvedName) {
    console.warn(`[property_resolver] missing match context=${context} desired=${desiredName}`);
    return null;
  }
  return resolvedName;
}

export function canUseProperty(
  properties: Record<string, any>,
  desiredName: string,
  type: NotionPropertyType,
  context: string,
  aliases: string[] = [],
): string | null {
  const resolvedName = resolvePropertyNameOrWarn(properties, desiredName, context, aliases);
  if (!resolvedName) {
    return null;
  }
  const schema = properties[resolvedName];
  if (!schema || schema.type !== type) {
    console.warn(
      `[property_resolver] type mismatch context=${context} desired=${desiredName} resolved=${resolvedName} expected=${type} actual=${schema?.type ?? "missing"}`,
    );
    return null;
  }
  return resolvedName;
}
