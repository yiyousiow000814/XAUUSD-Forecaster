import DashboardApp from "./DashboardApp";
import type { DashboardRoom } from "./DashboardNavigation";
import { previewAdminResources } from "../_lib/preview-resources";
import ArchitectureExplorerView from "../_views/ArchitectureExplorerView";

export default function AdminDashboardPage({ room }: { room: DashboardRoom | "architecture" }) {
  if (room === "architecture") return <ArchitectureExplorerView />;
  return <DashboardApp
    initialLocation={{ room, auditView: "news" }}
    initialResources={previewAdminResources()}
  />;
}
