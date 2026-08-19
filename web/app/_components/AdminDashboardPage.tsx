import DashboardApp from "./DashboardApp";
import type { DashboardRoom } from "./DashboardNavigation";

export default function AdminDashboardPage({ room }: { room: DashboardRoom }) {
  return <DashboardApp initialLocation={{ room, auditView: "news" }} />;
}
