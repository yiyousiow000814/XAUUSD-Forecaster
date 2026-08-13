export type CountFormat = "compact" | "exact";

export type CountPresentation = {
  accessibleValue: string;
  display: string;
  exact: string;
  title?: string;
};

const exactCount = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
  useGrouping: true,
});

const compactCount = new Intl.NumberFormat("en-US", {
  compactDisplay: "short",
  maximumFractionDigits: 1,
  notation: "compact",
});

function normalizedCount(value: number | null | undefined): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const count = Math.trunc(value);
  return Object.is(count, -0) ? 0 : count;
}

export function formatExactCount(value: number | null | undefined): string {
  const count = normalizedCount(value);
  return count === null ? "—" : exactCount.format(count);
}

export function formatCompactCount(value: number | null | undefined): string {
  const count = normalizedCount(value);
  return count === null ? "—" : compactCount.format(count);
}

export function formatCount(value: number | null | undefined, format: CountFormat = "compact"): string {
  return format === "exact" ? formatExactCount(value) : formatCompactCount(value);
}

export function formatProgressPair(
  current: number | null | undefined,
  target: number | null | undefined,
): string {
  const normalizedCurrent = normalizedCount(current);
  const normalizedTarget = normalizedCount(target);
  if (normalizedCurrent === null || normalizedTarget === null) return "— / —";

  const largest = Math.max(Math.abs(normalizedCurrent), Math.abs(normalizedTarget));
  if (largest < 1_000_000) {
    return `${exactCount.format(normalizedCurrent)} / ${exactCount.format(normalizedTarget)}`;
  }

  const [divisor, suffix] = largest >= 1_000_000_000_000
    ? [1_000_000_000_000, "万亿"]
    : largest >= 100_000_000
      ? [100_000_000, "亿"]
      : [10_000, "万"];
  const sharedScale = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
    useGrouping: false,
  });
  return `${sharedScale.format(normalizedCurrent / divisor)}${suffix} / ${sharedScale.format(normalizedTarget / divisor)}${suffix}`;
}

export function countPresentation(
  value: number | null | undefined,
  format: CountFormat = "compact",
  suffix = "",
): CountPresentation {
  const exact = formatExactCount(value);
  const display = formatCount(value, format);
  const accessibleValue = exact === "—" ? "暂无数据" : `${exact}${suffix}`;
  return {
    accessibleValue,
    display,
    exact,
    title: exact !== "—" && display !== exact ? `${exact}${suffix}` : undefined,
  };
}
