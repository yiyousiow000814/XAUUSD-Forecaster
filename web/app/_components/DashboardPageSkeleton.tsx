import type { DashboardLocation } from "./DashboardNavigation";

export function NewsListSkeleton({ label = "正在读取新闻" }: { label?: string } = {}) {
  return <div className="news-list-skeleton" role="status" aria-label={label}>
    <span className="sr-only">{label}</span>
    <div aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <div className="news-skeleton-row" key={index}>
      <i className="skeleton-block skeleton-meta" /><i className="skeleton-block skeleton-headline" /><i className="skeleton-block skeleton-source" />
    </div>)}</div>
  </div>;
}

export default function DashboardPageSkeleton({ location }: { location: DashboardLocation }) {
  const audit = location.room === "audit";
  return <main className="app-view-loading page-skeleton" aria-busy="true" aria-label={audit ? "新闻与事件加载中" : "页面加载中"}>
    {audit && <div className="skeleton-navigation" aria-hidden="true">{Array.from({length:6},(_,index)=><i className="skeleton-block" key={index} />)}</div>}
    <div className="skeleton-heading" aria-hidden="true"><i className="skeleton-block" /><i className="skeleton-block" /></div>
    <NewsListSkeleton label={audit ? "正在读取新闻与事件" : "正在打开页面"} />
  </main>;
}
