const NEW_YORK_TIME_ZONE = "America/New_York";
const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

const newYorkParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: NEW_YORK_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function localParts(value: number): Record<string, number> {
  return Object.fromEntries(
    newYorkParts.formatToParts(new Date(value))
      .filter(part => part.type !== "literal")
      .map(part => [part.type, Number(part.value)]),
  );
}

function newYorkWallTimeUtc(
  year: number,
  month: number,
  day: number,
  hour: number,
): number {
  const desired = Date.UTC(year, month - 1, day, hour);
  let candidate = desired;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const actual = localParts(candidate);
    const represented = Date.UTC(
      actual.year,
      actual.month - 1,
      actual.day,
      actual.hour,
      actual.minute,
      actual.second,
    );
    candidate += desired - represented;
  }
  return candidate;
}

export function marketOpenElapsedMs(start: number, end: number): number {
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
    throw new RangeError("end must not be before start");
  }
  const startLocal = localParts(start);
  const startDate = Date.UTC(startLocal.year, startLocal.month - 1, startLocal.day) - 7 * DAY_MS;
  const endLocal = localParts(end);
  const endDate = Date.UTC(endLocal.year, endLocal.month - 1, endLocal.day);
  let closedMs = 0;
  for (let cursor = startDate; cursor <= endDate; cursor += DAY_MS) {
    const date = new Date(cursor);
    if (date.getUTCDay() !== 5) continue;
    const close = newYorkWallTimeUtc(
      date.getUTCFullYear(),
      date.getUTCMonth() + 1,
      date.getUTCDate(),
      17,
    );
    const reopenDate = new Date(cursor + 2 * DAY_MS);
    const reopen = newYorkWallTimeUtc(
      reopenDate.getUTCFullYear(),
      reopenDate.getUTCMonth() + 1,
      reopenDate.getUTCDate(),
      18,
    );
    const overlapStart = Math.max(start, close);
    const overlapEnd = Math.min(end, reopen);
    if (overlapEnd > overlapStart) closedMs += overlapEnd - overlapStart;
  }
  return end - start - closedMs;
}

export function buildMarketOpenResultWindows(
  resultTimes: number[],
  durationMs: number,
): Array<{ start: number; end: number }> {
  if (!Number.isFinite(durationMs) || durationMs <= 0) {
    throw new RangeError("duration must be positive");
  }
  const windows: Array<{ start: number; end: number }> = [];
  let endIndex = resultTimes.length - 1;
  while (endIndex >= 0) {
    const end = resultTimes[endIndex];
    let startIndex = endIndex;
    while (
      startIndex > 0
      && marketOpenElapsedMs(resultTimes[startIndex - 1], end) <= durationMs
    ) {
      startIndex -= 1;
    }
    windows.push({ start: resultTimes[startIndex], end });
    endIndex = startIndex - 1;
  }
  return windows;
}
