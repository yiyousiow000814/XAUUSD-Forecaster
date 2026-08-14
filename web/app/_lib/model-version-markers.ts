export type VersionedDecision = {
  decision_time: string;
  model_version: string;
};

export type ModelVersionMarker = {
  decision_time: string;
  previous_model_version: string;
  model_version: string;
};

/**
 * Identify version handovers from the predictions actually visible in a chart.
 * The first row establishes the baseline; only a later version change is a
 * handover inside the selected window.
 */
export function modelVersionMarkers(
  decisions: VersionedDecision[],
): ModelVersionMarker[] {
  const ordered = [...decisions]
    .filter(row => row.decision_time && row.model_version)
    .sort((left, right) => Date.parse(left.decision_time) - Date.parse(right.decision_time));
  const markers: ModelVersionMarker[] = [];
  let previous = ordered[0]?.model_version;
  for (const row of ordered.slice(1)) {
    if (row.model_version === previous) continue;
    markers.push({
      decision_time: row.decision_time,
      previous_model_version: previous,
      model_version: row.model_version,
    });
    previous = row.model_version;
  }
  return markers;
}
