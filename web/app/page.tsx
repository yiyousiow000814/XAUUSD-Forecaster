import DashboardApp from "./_components/DashboardApp";
import { previewResources } from "./_lib/preview-resources";

export const dynamic = "force-static";

export default function HomePage() {
  const initialResources = previewResources();
  return <DashboardApp
    initialLocation={{ room: "live", auditView: "news" }}
    initialResources={initialResources}
  />;
}
