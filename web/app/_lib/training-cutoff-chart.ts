export type TrainingCutoffRow = {
  model_identity: string;
  generation: number;
};

export type TrainingCutoffPoint<T extends TrainingCutoffRow> = {
  cutoff: number;
  cutoffIndex: number;
  row: T;
};

export function buildTrainingCutoffChart<T extends TrainingCutoffRow>(
  rows: T[],
  cutoffFor: (row: T) => number,
  maximumCutoffs?: number,
) {
  const allCutoffs = [...new Set(rows.map(cutoffFor))].sort((a, b) => a - b);
  const cutoffs = maximumCutoffs && allCutoffs.length > maximumCutoffs
    ? allCutoffs.slice(-maximumCutoffs)
    : allCutoffs;
  const cutoffIndexes = new Map(cutoffs.map((cutoff, index) => [cutoff, index]));
  const identities = [...new Set(rows.map(row => row.model_identity))];
  const series = identities.map(modelIdentity => ({
    modelIdentity,
    points: rows
      .filter(row => row.model_identity === modelIdentity && cutoffIndexes.has(cutoffFor(row)))
      .sort((a, b) => cutoffFor(a) - cutoffFor(b) || a.generation - b.generation)
      .map(row => ({
        cutoff: cutoffFor(row),
        cutoffIndex: cutoffIndexes.get(cutoffFor(row))!,
        row,
      })),
  }));
  return { cutoffs, series };
}
