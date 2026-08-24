import { publicDashboardStatus } from "../api/_shared/dashboard-status";
import { previewBundle } from "../api/_shared/preview";
import { PREVIEW_NEWS_PAGE_SIZE, PREVIEW_RESOURCES } from "./preview-manifest";

export function previewResources(): Record<string, unknown> {
  if (!previewBundle) return {};
  const resources: Record<string, unknown> = {
    [PREVIEW_RESOURCES.status]: publicDashboardStatus(previewBundle.status),
  };
  if (previewBundle.audit) resources[PREVIEW_RESOURCES.audit] = previewBundle.audit;
  resources[
    `${PREVIEW_RESOURCES.newsIndex}?page=1&limit=${PREVIEW_NEWS_PAGE_SIZE}&review_state=COMPLETED`
  ] = previewBundle.news_index;
  if (previewBundle.learning_summary) {
    resources[PREVIEW_RESOURCES.learning] = previewBundle.learning_summary;
  }
  return resources;
}

export function previewAdminResources(): Record<string, unknown> {
  const resources = previewResources();
  if (previewBundle) resources["/admin/api/admin-status"] = previewBundle.status;
  return resources;
}
