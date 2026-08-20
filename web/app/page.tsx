import DashboardApp from "./_components/DashboardApp";
import { previewBundle } from "./api/_shared/preview";
import { publicDashboardStatus } from "./api/_shared/dashboard-status";
import { PREVIEW_NEWS_PAGE_SIZE, PREVIEW_RESOURCES } from "./_lib/preview-manifest";

export const dynamic = "force-static";

function previewResources(): Record<string, unknown> {
  if (!previewBundle) return {};
  const resources: Record<string, unknown> = {
    [PREVIEW_RESOURCES.status]: publicDashboardStatus(previewBundle.status),
  };
  if (previewBundle.audit) resources[PREVIEW_RESOURCES.audit] = previewBundle.audit;
  resources[`${PREVIEW_RESOURCES.newsIndex}?page=1&limit=${PREVIEW_NEWS_PAGE_SIZE}&review_state=COMPLETED`] = previewBundle.news_index;

  const learning = previewBundle.learning_summary;
  if (!learning) return resources;
  resources[PREVIEW_RESOURCES.learning] = learning;
  return resources;
}

export default function HomePage() {
  const initialResources = previewResources();
  return <DashboardApp
    initialLocation={{ room: "live", auditView: "news" }}
    initialResources={initialResources}
  />;
}
