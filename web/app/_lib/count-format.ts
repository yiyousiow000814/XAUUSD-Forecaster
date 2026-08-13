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
