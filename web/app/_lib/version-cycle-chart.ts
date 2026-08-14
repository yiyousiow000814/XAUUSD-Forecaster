export type VersionCycleRow = {
  model_identity: string;
  created_at: string;
  generation: number;
};

export type VersionCyclePoint<T extends VersionCycleRow> = {
  cycle: string;
  cycleIndex: number;
  row: T;
};

export function buildVersionCycleChart<T extends VersionCycleRow>(
  rows: T[],
  maximumCycles?: number,
) {
  const allCycles = [...new Set(rows.map(row => row.created_at))]
    .sort((a, b) => Date.parse(a) - Date.parse(b));
  const cycles = maximumCycles && allCycles.length > maximumCycles
    ? allCycles.slice(-maximumCycles)
    : allCycles;
  const cycleIndexes = new Map(cycles.map((cycle, index) => [cycle, index]));
  const latestAtCycle = new Map<string, T>();
  for (const row of [...rows].sort((a, b) => a.generation - b.generation)) {
    if (!cycleIndexes.has(row.created_at)) continue;
    latestAtCycle.set(`${row.model_identity}\u0000${row.created_at}`, row);
  }
  const identities = [...new Set(rows.map(row => row.model_identity))];
  const series = identities.map(modelIdentity => ({
    modelIdentity,
    points: cycles.flatMap((cycle, cycleIndex) => {
      const row = latestAtCycle.get(`${modelIdentity}\u0000${cycle}`);
      return row ? [{ cycle, cycleIndex, row }] : [];
    }),
  }));
  return { cycles, series };
}
