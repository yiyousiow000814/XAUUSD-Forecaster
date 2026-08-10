export type CompressedTimeline = {
  positionAt: (time: string | number) => number;
  positions: number[];
};

/**
 * One plotting-time contract for every market chart.
 *
 * Real observations keep their order, while long periods without observations
 * consume at most `maxGapUnits` slots.  The caller decides whether a missing
 * interval needs a visual data-quality marker; the time axis itself never
 * stretches a weekend across most of the chart.
 */
export function compressedTimeline(
  rawTimes: Array<string | number>,
  expectedStepMs: number,
  startX: number,
  endX: number,
  maxGapUnits = 4,
): CompressedTimeline {
  if (!(expectedStepMs > 0)) throw new Error("expectedStepMs must be positive");
  const times = rawTimes.map(value => typeof value === "number" ? value : Date.parse(value));
  if (times.some(time => !Number.isFinite(time))) throw new Error("timeline contains an invalid time");
  const units = new Array<number>(times.length);
  if (times.length) units[0] = 0;
  for (let index = 1; index < times.length; index += 1) {
    const elapsed = times[index] - times[index - 1];
    if (!(elapsed > 0)) throw new Error("timeline times must be strictly increasing");
    units[index] = units[index - 1] + Math.min(maxGapUnits, Math.max(1, elapsed / expectedStepMs));
  }
  const totalUnits = Math.max(1, units.at(-1) ?? 1);
  const positions = units.map(value => startX + value / totalUnits * (endX - startX));
  const byTime = new Map(times.map((time, index) => [time, positions[index]]));
  return {
    positions,
    positionAt: value => byTime.get(typeof value === "number" ? value : Date.parse(value)) ?? startX,
  };
}
