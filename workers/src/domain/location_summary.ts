export type NormalizedLocationLog = {
  timeIso: string;
  timeMs: number;
  place: string;
  lat: number | null;
  lon: number | null;
  source: string;
};

export type LocationSegment = {
  startMs: number;
  endMs: number;
  startTime: string;
  endTime: string;
  placeRaw: string;
  placeLabel: string;
  lat: number | null;
  lon: number | null;
  durationMin: number;
  points: number;
};

export type LocationSummaryStats = {
  window_start: string;
  window_end: string;
  move_count: number;
  first_seen: string;
  last_seen: string;
  top_places: Array<{ place_label: string; duration_min: number; visits: number }>;
  data_quality_notes: string[];
};

export type LocationSummaryResult = {
  location_summary_text: string;
  primary_place_label: string;
  stats: LocationSummaryStats;
};

export type LocationWindow = {
  anchorStartIso: string;
  anchorEndIso: string;
  diaryDate: string;
};

export function addDaysToDateString(dateString: string, days: number): string {
  const date = new Date(`${dateString}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function formatJstTime(ms: number): string {
  const formatter = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return formatter.format(new Date(ms));
}

function getJstDateParts(now: Date): { date: string; hour: number } {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(now);
  const byType: Record<string, string> = {};
  parts.forEach((part) => {
    byType[part.type] = part.value;
  });
  return {
    date: `${byType.year}-${byType.month}-${byType.day}`,
    hour: Number(byType.hour || "0"),
  };
}

export function resolveLocationWindow(now: Date, windowStartHour: number): LocationWindow {
  const { date: nowDate, hour: nowHour } = getJstDateParts(now);
  const anchorEndDate = nowHour >= windowStartHour ? nowDate : addDaysToDateString(nowDate, -1);
  const anchorStartDate = addDaysToDateString(anchorEndDate, -1);
  const hh = String(windowStartHour).padStart(2, "0");
  return {
    anchorStartIso: `${anchorStartDate}T${hh}:00:00+09:00`,
    anchorEndIso: `${anchorEndDate}T${hh}:00:00+09:00`,
    diaryDate: addDaysToDateString(anchorEndDate, -1),
  };
}

function toRoundedKey(value: number | null, decimals: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  return value.toFixed(decimals);
}

function placeLabel(placeRaw: string): string {
  const trimmed = placeRaw.trim();
  if (!trimmed) {
    return "場所不明";
  }
  return trimmed.length <= 28 ? trimmed : trimmed.slice(0, 28);
}

function isSameLocation(
  current: NormalizedLocationLog,
  previous: NormalizedLocationLog,
  decimals: number,
): boolean {
  const currentLat = toRoundedKey(current.lat, decimals);
  const currentLon = toRoundedKey(current.lon, decimals);
  const previousLat = toRoundedKey(previous.lat, decimals);
  const previousLon = toRoundedKey(previous.lon, decimals);

  if (currentLat && currentLon && previousLat && previousLon) {
    return currentLat === previousLat && currentLon === previousLon;
  }

  if (current.place && previous.place) {
    return current.place === previous.place;
  }

  return false;
}

export function segmentLocationLogs(
  logs: NormalizedLocationLog[],
  roundDecimals: number,
  timeBucketMinutes: number,
): { segments: LocationSegment[]; moveCount: number } {
  if (!logs.length) {
    return { segments: [], moveCount: 0 };
  }

  const sorted = [...logs].sort((a, b) => a.timeMs - b.timeMs);
  const rawSegments: Array<{ startIndex: number; endIndex: number }> = [];

  for (let i = 0; i < sorted.length; i += 1) {
    if (!rawSegments.length) {
      rawSegments.push({ startIndex: i, endIndex: i });
      continue;
    }
    const previousSegment = rawSegments[rawSegments.length - 1];
    const previousLog = sorted[previousSegment.endIndex];
    if (isSameLocation(sorted[i], previousLog, roundDecimals)) {
      previousSegment.endIndex = i;
    } else {
      rawSegments.push({ startIndex: i, endIndex: i });
    }
  }

  const bucketMs = Math.max(1, timeBucketMinutes) * 60 * 1000;
  const timelineEndMs = sorted[sorted.length - 1].timeMs;
  const segments: LocationSegment[] = rawSegments.map((segment, index) => {
    const startLog = sorted[segment.startIndex];
    const nextStartMs =
      index < rawSegments.length - 1
        ? sorted[rawSegments[index + 1].startIndex].timeMs
        : timelineEndMs;
    const startMs = Math.floor(startLog.timeMs / bucketMs) * bucketMs;
    const endMs = Math.floor(nextStartMs / bucketMs) * bucketMs;
    const durationMin = Math.max(0, Math.round((endMs - startMs) / 60000));

    return {
      startMs,
      endMs,
      startTime: formatJstTime(startMs),
      endTime: formatJstTime(endMs),
      placeRaw: startLog.place,
      placeLabel: placeLabel(startLog.place),
      lat: startLog.lat,
      lon: startLog.lon,
      durationMin,
      points: segment.endIndex - segment.startIndex + 1,
    };
  });

  return {
    segments,
    moveCount: Math.max(0, segments.length - 1),
  };
}

function buildTimelineLine(segment: LocationSegment): string {
  return `- ${segment.startTime}–${segment.endTime} ${segment.placeLabel}（${segment.durationMin}分）`;
}

export function buildFallbackLocationSummary(
  windowStartIso: string,
  windowEndIso: string,
  segments: LocationSegment[],
  moveCount: number,
  dataQualityNotes: string[],
): LocationSummaryResult {
  const header = `（前日05:00〜当日05:00）`;

  if (!segments.length) {
    return {
      location_summary_text: `${header}\nこの時間帯は位置ログがありませんでした。`,
      primary_place_label: "",
      stats: {
        window_start: windowStartIso,
        window_end: windowEndIso,
        move_count: 0,
        first_seen: "",
        last_seen: "",
        top_places: [],
        data_quality_notes: dataQualityNotes,
      },
    };
  }

  if (segments.length === 1) {
    const only = segments[0];
    return {
      location_summary_text: `${header}\n${only.startTime}ごろに${only.placeLabel}の記録がありました。\nタイムライン:\n${buildTimelineLine(only)}`,
      primary_place_label: only.placeLabel,
      stats: {
        window_start: windowStartIso,
        window_end: windowEndIso,
        move_count: 0,
        first_seen: only.startTime,
        last_seen: only.endTime,
        top_places: [{ place_label: only.placeLabel, duration_min: only.durationMin, visits: 1 }],
        data_quality_notes: dataQualityNotes,
      },
    };
  }

  const grouped = new Map<string, { duration: number; visits: number }>();
  for (const segment of segments) {
    const prev = grouped.get(segment.placeLabel) || { duration: 0, visits: 0 };
    grouped.set(segment.placeLabel, {
      duration: prev.duration + segment.durationMin,
      visits: prev.visits + 1,
    });
  }

  const topPlaces = Array.from(grouped.entries())
    .map(([label, value]) => ({
      place_label: label,
      duration_min: value.duration,
      visits: value.visits,
    }))
    .sort((a, b) => b.duration_min - a.duration_min)
    .slice(0, 3);

  const primary = topPlaces[0]?.place_label || segments[0].placeLabel;
  const lines = [
    header,
    `${segments[0].startTime}ごろは${segments[0].placeLabel}付近にいて、その後は場所を移動しながら過ごした。`,
    `記録上の移動回数は${moveCount}回で、最後は${segments[segments.length - 1].placeLabel}付近にいた。`,
    "タイムライン:",
    ...segments.map(buildTimelineLine),
  ];

  return {
    location_summary_text: lines.join("\n"),
    primary_place_label: primary,
    stats: {
      window_start: windowStartIso,
      window_end: windowEndIso,
      move_count: moveCount,
      first_seen: segments[0].startTime,
      last_seen: segments[segments.length - 1].endTime,
      top_places: topPlaces,
      data_quality_notes: dataQualityNotes,
    },
  };
}
