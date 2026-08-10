import DashboardApp from "./_components/DashboardApp";
import type { AuditViewName, DashboardLocation } from "./_components/DashboardNavigation";
import { previewBundle } from "./api/_shared/preview";

type PageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const AUDIT_VIEWS = new Set<AuditViewName>(["news", "evidence", "stories", "decisions", "league", "coverage"]);

function previewLeagueResources(): Record<string, unknown> {
  if (!previewBundle) return {};
  const status = previewBundle.status;
  const learning = previewBundle.learning;
  const curves = (learning.learning_curves ?? {}) as Record<string, unknown>;
  const models = Array.isArray(curves.models) ? curves.models : [];
  const versionGroups = Array.isArray(curves.version_groups) ? curves.version_groups : [];
  const compactStatus = {
    generated_at: status.generated_at,
    system: status.system,
    training: status.training,
    counts: status.counts,
    news_evidence_summary: status.news_evidence_summary,
    storyline_summary: status.storyline_summary,
    factor_coverage: status.factor_coverage,
  };
  const compactCurves = {
    collection_epoch: curves.collection_epoch,
    evaluation_epoch_v2: curves.evaluation_epoch_v2,
    learning_stage: curves.learning_stage,
    models: models.filter(row => (
      row && typeof row === "object" && (row as Record<string, unknown>).active_rank !== null
    )),
    version_groups: versionGroups.filter(row => (
      row && typeof row === "object"
      && (row as Record<string, unknown>).lifecycle_status === "LATEST"
    )),
    rolling_processes: curves.rolling_processes,
    identity_curves: curves.identity_curves,
  };
  return {
    "/api/status": compactStatus,
    "/api/learning": {
      generated_at: learning.generated_at,
      learning_curves: compactCurves,
    },
  };
}

export default async function HomePage({ searchParams }: PageProps) {
  const query = await searchParams;
  const roomValue = Array.isArray(query.room) ? query.room[0] : query.room;
  const viewValue = Array.isArray(query.view) ? query.view[0] : query.view;
  const auditView = viewValue && AUDIT_VIEWS.has(viewValue as AuditViewName) ? viewValue as AuditViewName : "news";
  const room = roomValue === "status" || roomValue === "health" || roomValue === "audit" ? roomValue : "live";
  const initialLocation: DashboardLocation = { room, auditView };
  const initialResources = room === "audit" && auditView === "league"
    ? previewLeagueResources()
    : {};
  return <DashboardApp initialLocation={initialLocation} initialResources={initialResources} />;
}
