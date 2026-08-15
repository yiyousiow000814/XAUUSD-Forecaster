"use client";

import type { ReactNode } from "react";
import { useDashboardNavigation } from "./DashboardNavigation";

export type MobileDashboardSection = "live" | "assistant" | "audit" | "learning" | "status" | "health";

const SECTIONS: Array<{ href: string; label: string; value: MobileDashboardSection }> = [
  { href: "/", label: "实时室", value: "live" },
  { href: "/assistant", label: "Assistant 私有分析", value: "assistant" },
  { href: "/audit?view=news", label: "新闻与证据", value: "audit" },
  { href: "/audit?view=league", label: "学习曲线", value: "learning" },
  { href: "/status", label: "AI 模型用量", value: "status" },
  { href: "/health", label: "系统健康", value: "health" },
];

export default function MobileDashboardNav({
  current,
  status,
}: {
  current: MobileDashboardSection;
  status?: ReactNode;
}) {
  const navigation = useDashboardNavigation();
  const currentHref = SECTIONS.find(section => section.value === current)?.href ?? "/";

  return <div className="mobile-dashboard-nav">
    <label>
      <span>主要区域</span>
      <select
        aria-label="切换主要区域"
        value={currentHref}
        onChange={event => {
          const href = event.currentTarget.value;
          if (navigation) void navigation.navigate(href);
          else window.location.assign(href);
        }}
      >
        {SECTIONS.map(section => <option key={section.value} value={section.href}>{section.label}</option>)}
      </select>
    </label>
    {status && <div className="mobile-dashboard-status">{status}</div>}
  </div>;
}
