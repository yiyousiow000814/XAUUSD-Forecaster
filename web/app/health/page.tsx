import type { Metadata } from "next";
import DashboardApp from "../_components/DashboardApp";
import { previewResources } from "../_lib/preview-resources";

export const dynamic = "force-static";
export const metadata: Metadata = {
  title: "系统健康状态 | Aurum Signal Room",
};

export default function HealthPage() {
  return <>
    <noscript><main><h1>系统健康状态</h1><p>启用 JavaScript 后可查看完整系统健康证据。</p></main></noscript>
    <DashboardApp
      initialLocation={{ room: "health", auditView: "news" }}
      initialResources={previewResources()}
    />
  </>;
}
