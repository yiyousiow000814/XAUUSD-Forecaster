import DashboardApp from "./_components/DashboardApp";
import type { AuditViewName, DashboardLocation } from "./_components/DashboardNavigation";
import { previewBundle } from "./api/_shared/preview";

type PageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const AUDIT_VIEWS = new Set<AuditViewName>(["news", "evidence", "stories", "decisions", "league", "coverage"]);

function previewStatusResource(): Record<string, unknown> | null {
  if (!previewBundle) return null;
  const status = previewBundle.status;
  const compact: Record<string, unknown> = {};
  for (const key of [
    "generated_at", "system", "training", "counts", "news_evidence_summary",
    "storyline_summary", "factor_coverage", "annotation_queue", "news_evidence",
    "recent_decisions", "storylines", "market_narrative_candidates", "archived_storylines",
    "archived_story_event_candidates", "story_event_candidates", "market_reaction_streams",
    "theme_streams", "unassigned_story_events", "news_source_health", "llm_routing",
    "gemini_quota", "gemini_31_quota", "gemma_quota", "latest", "research_forecast",
    "u5_context", "outcome_summary", "sources", "forward_epoch",
  ]) compact[key] = status[key];
  return compact;
}

function previewAuditResources(auditView: AuditViewName): Record<string, unknown> {
  const compactStatus = previewStatusResource();
  if (!previewBundle || !compactStatus) return {};
  const resources: Record<string, unknown> = { "/api/status": compactStatus };

  if (auditView === "news") {
    const index = previewBundle.news_index;
    const items = Array.isArray(index.items) ? index.items : [];
    resources["/api/news-index?page=1&limit=12"] = {
      ...index, items: items.slice(0, 12), page: 1, page_size: 12,
    };
  }
  if (auditView !== "league") return resources;

  const learning = previewBundle.learning;
  const curves = (learning.learning_curves ?? {}) as Record<string, unknown>;
  const models = Array.isArray(curves.models) ? curves.models : [];
  const versionGroups = Array.isArray(curves.version_groups) ? curves.version_groups : [];
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
  resources["/api/learning"] = {
    generated_at: learning.generated_at,
    learning_curves: compactCurves,
  };
  return resources;
}

function previewRoomResources(room: DashboardLocation["room"], auditView: AuditViewName): Record<string, unknown> {
  if (room === "audit") return previewAuditResources(auditView);
  const compactStatus = previewStatusResource();
  if (!compactStatus) return {};
  return { "/api/status": compactStatus };
}

export default async function HomePage({ searchParams }: PageProps) {
  const query = await searchParams;
  const roomValue = Array.isArray(query.room) ? query.room[0] : query.room;
  const viewValue = Array.isArray(query.view) ? query.view[0] : query.view;
  const auditView = viewValue && AUDIT_VIEWS.has(viewValue as AuditViewName) ? viewValue as AuditViewName : "news";
  const room = roomValue === "status" || roomValue === "health" || roomValue === "audit" ? roomValue : "live";
  const initialLocation: DashboardLocation = { room, auditView };
  const initialResources = previewRoomResources(room, auditView);
  return <DashboardApp initialLocation={initialLocation} initialResources={initialResources} />;
}
