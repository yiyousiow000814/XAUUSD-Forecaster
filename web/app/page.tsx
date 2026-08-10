import DashboardApp from "./_components/DashboardApp";
import type { AuditViewName, DashboardLocation } from "./_components/DashboardNavigation";
import { previewBundle } from "./api/_shared/preview";

type PageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const AUDIT_VIEWS = new Set<AuditViewName>(["news", "evidence", "stories", "decisions", "league", "coverage"]);

export default async function HomePage({ searchParams }: PageProps) {
  const query = await searchParams;
  const roomValue = Array.isArray(query.room) ? query.room[0] : query.room;
  const viewValue = Array.isArray(query.view) ? query.view[0] : query.view;
  const auditView = viewValue && AUDIT_VIEWS.has(viewValue as AuditViewName) ? viewValue as AuditViewName : "news";
  const room = roomValue === "status" || roomValue === "health" || roomValue === "audit" ? roomValue : "live";
  const initialLocation: DashboardLocation = { room, auditView };
  const initialResources: Record<string, unknown> = previewBundle
    ? { "/api/status": previewBundle.status }
    : {};
  if (previewBundle && room === "audit" && auditView === "league") {
    initialResources["/api/learning"] = previewBundle.learning;
  }
  return <DashboardApp initialLocation={initialLocation} initialResources={initialResources} />;
}
