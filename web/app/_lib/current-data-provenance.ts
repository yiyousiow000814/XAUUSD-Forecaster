export type DataPhase = "loading" | "ready" | "snapshot" | "error";

/** Resolve one status field without treating a mixed-provenance payload as all current. */
export function statusFieldPhase(
  overallPhase: DataPhase,
  branchSnapshotKeys: readonly string[] | undefined,
  key: string,
): DataPhase {
  if (overallPhase !== "ready") return overallPhase;
  return branchSnapshotKeys?.includes(key) ? "snapshot" : "ready";
}
