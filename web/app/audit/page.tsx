import type { Metadata } from "next";
import DashboardApp from "../_components/DashboardApp";
import { previewResources } from "../_lib/preview-resources";

export const dynamic = "force-static";
export const metadata: Metadata = {
  title: "证据台页面 | Aurum Signal Room",
};

export default function AuditPage() {
  return <>
    <noscript><main><h1>证据台页面</h1><p>启用 JavaScript 后可查看完整新闻与决策证据。</p></main></noscript>
    <DashboardApp
      initialLocation={{ room: "audit", auditView: "news" }}
      initialResources={previewResources()}
    />
  </>;
}
