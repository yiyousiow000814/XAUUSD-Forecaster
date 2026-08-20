import DashboardApp from "../_components/DashboardApp";
import { previewResources } from "../_lib/preview-resources";

export const dynamic = "force-static";

export default function HealthPage() {
  return <DashboardApp
    initialLocation={{ room: "health", auditView: "news" }}
    initialResources={previewResources()}
  />;
}
