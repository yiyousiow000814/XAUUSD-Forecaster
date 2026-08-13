export type CountFormat = "compact" | "exact";

export type CountPresentation = {
  accessibleValue: string;
  display: string;
  exact: string;
  title?: string;
};

export type ProgressCountPresentation = {
  current: ProgressCountValue;
  isAbbreviated: boolean;
  showExactDetail: boolean;
  target: ProgressCountValue;
};

export type ProgressCountValue = {
  exact: string;
  main: string;
  remainder?: string;
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

export function progressCountPresentation(
  current: number | null | undefined,
  target: number | null | undefined,
): ProgressCountPresentation {
  const normalizedCurrent = normalizedCount(current);
  const normalizedTarget = normalizedCount(target);
  if (normalizedCurrent === null || normalizedTarget === null) {
    return {
      current: { exact: "—", main: "—" },
      isAbbreviated: false,
      showExactDetail: false,
      target: { exact: "—", main: "—" },
    };
  }

  const currentExact = exactCount.format(normalizedCurrent);
  const targetExact = exactCount.format(normalizedTarget);

  const largest = Math.max(Math.abs(normalizedCurrent), Math.abs(normalizedTarget));
  if (largest < 10_000) {
    return {
      current: { exact: currentExact, main: currentExact },
      isAbbreviated: false,
      showExactDetail: false,
      target: { exact: targetExact, main: targetExact },
    };
  }

  const [divisor, suffix] = largest >= 1_000_000_000_000
    ? [1_000_000_000_000, "T"]
    : largest >= 1_000_000_000
      ? [1_000_000_000, "B"]
      : largest >= 1_000_000
        ? [1_000_000, "M"]
        : [1_000, "K"];
  const step = divisor / 10;
  const sharedScale = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 1,
    useGrouping: false,
  });
  const compactValue = (value: number, exact: string): ProgressCountValue => {
    const base = Math.trunc(value / step) * step;
    const remainder = value - base;
    return {
      exact,
      main: `${sharedScale.format(base / divisor)}${suffix}`,
      ...(remainder > 0 && remainder <= 999 ? { remainder: exactCount.format(remainder) } : {}),
    };
  };
  const currentValue = compactValue(normalizedCurrent, currentExact);
  const targetValue = compactValue(normalizedTarget, targetExact);
  return {
    current: currentValue,
    isAbbreviated: true,
    showExactDetail: (
      normalizedCurrent - Math.trunc(normalizedCurrent / step) * step > 999
      || normalizedTarget - Math.trunc(normalizedTarget / step) * step > 999
    ),
    target: targetValue,
  };
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
