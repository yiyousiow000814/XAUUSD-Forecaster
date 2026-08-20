import DashboardApp from "../_components/DashboardApp";
import { previewResources } from "../_lib/preview-resources";

export const dynamic = "force-static";

export default function AuditPage() {
  return <DashboardApp
    initialLocation={{ room: "audit", auditView: "news" }}
    initialResources={previewResources()}
  />;
}
