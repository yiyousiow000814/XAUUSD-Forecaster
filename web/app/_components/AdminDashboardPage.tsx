import DashboardApp from "./DashboardApp";
import type { DashboardRoom } from "./DashboardNavigation";
import { previewAdminResources } from "../_lib/preview-resources";

export default function AdminDashboardPage({ room }: { room: DashboardRoom }) {
  return <DashboardApp
    initialLocation={{ room, auditView: "news" }}
    initialResources={previewAdminResources()}
  />;
}
