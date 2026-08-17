const OPERATOR_TIME_ZONE = "Asia/Kuala_Lumpur";
const TIMESTAMP_KEY = /(?:^|_)(?:at|time)$/;

const timestampFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: OPERATOR_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function readableTimestamp(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return null;
  const parts = Object.fromEntries(
    timestampFormatter.formatToParts(instant).map(part => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} UTC+8`;
}

export function operationalEvidenceValue(key: string, value: unknown): string {
  if (TIMESTAMP_KEY.test(key)) {
    const timestamp = readableTimestamp(value);
    if (timestamp) return timestamp;
  }
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function operationalEvidenceText(evidence: Record<string, unknown>): string {
  return Object.entries(evidence)
    .map(([key, value]) => `${key}=${operationalEvidenceValue(key, value)}`)
    .join(" · ");
}
