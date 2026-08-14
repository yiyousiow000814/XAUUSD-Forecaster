export type DataPhase = "loading" | "ready" | "snapshot" | "error";

/** Resolve one status field without treating a mixed-provenance payload as all current. */
export function statusFieldPhase(
  overallPhase: DataPhase,
  branchSnapshotPaths: readonly string[] | undefined,
  path: string,
): DataPhase {
  if (overallPhase !== "ready") return overallPhase;
  return branchSnapshotPaths?.includes(path) ? "snapshot" : "ready";
}
