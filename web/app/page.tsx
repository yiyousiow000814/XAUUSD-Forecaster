import DashboardApp from "./_components/DashboardApp";
import type { AuditViewName, DashboardLocation } from "./_components/DashboardNavigation";
import { redirect } from "next/navigation";
import { previewBundle } from "./api/_shared/preview";
import { publicDashboardStatus } from "./api/_shared/dashboard-status";
import { PREVIEW_NEWS_PAGE_SIZE, PREVIEW_RESOURCES } from "./_lib/preview-manifest";

type PageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const AUDIT_VIEWS = new Set<AuditViewName>(["news", "evidence", "stories", "decisions", "league", "coverage"]);

function previewResources(): Record<string, unknown> {
  if (!previewBundle) return {};
  const resources: Record<string, unknown> = {
    [PREVIEW_RESOURCES.status]: publicDashboardStatus(previewBundle.status),
  };
  resources[`${PREVIEW_RESOURCES.newsIndex}?page=1&limit=${PREVIEW_NEWS_PAGE_SIZE}&review_state=COMPLETED`] = previewBundle.news_index;

  const learning = previewBundle.learning_summary;
  if (!learning) return resources;
  resources[PREVIEW_RESOURCES.learning] = learning;
  return resources;
}

export default async function HomePage({ searchParams }: PageProps) {
  const query = await searchParams;
  const roomValue = Array.isArray(query.room) ? query.room[0] : query.room;
  if (roomValue === "assistant") redirect("/admin/assistant");
  if (roomValue === "retry") redirect("/admin/retry-jobs");
  if (roomValue === "status") redirect("/admin/ai-usage");
  const viewValue = Array.isArray(query.view) ? query.view[0] : query.view;
  const auditView = viewValue && AUDIT_VIEWS.has(viewValue as AuditViewName) ? viewValue as AuditViewName : "news";
  const room = roomValue === "health" || roomValue === "audit" ? roomValue : "live";
  const initialLocation: DashboardLocation = { room, auditView };
  const initialResources = previewResources();
  return <DashboardApp initialLocation={initialLocation} initialResources={initialResources} />;
}
