"use client";

import { useCallback, useEffect, useState } from "react";
import { ReaderIcon, BarChartIcon, ChevronRightIcon, ArrowRightIcon } from "@radix-ui/react-icons";
import DashboardLink from "./DashboardLink";
import { validAuditDetailPayload } from "../_lib/audit-detail-contract";
import { publicBriefText } from "../_lib/public-news-copy";
import { loadDashboardResource, readDashboardResourceState, subscribeDashboardResource } from "../_lib/dashboard-resource";
import { scheduleDashboardRefresh } from "../_lib/dashboard-refresh";

declare const __AURUM_DEPLOYMENT__: { is_preview: boolean };

type Briefs = { daily_news_briefs: Array<{ brief_date: string; revision_number: number; brief: { items: Array<{ headline: string; summary: string }> } }> };
type Events = { items: Array<{ event_key: string; canonical_headline: string; source_published_time?: string | null }> };
type ReadState<T> = { data: T | null; error: Error | null };
const BRIEFS = "/api/audit-briefs";
const EVENTS = "/api/news-evidence?mode=eligible&page=1&limit=3";
const INTERVAL = 300_000;
const eventTimeFormat = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  hour12: false, timeZone: "Asia/Kuala_Lumpur",
});

function EventTime({ value }: { value?: string | null }) {
  const timestamp = typeof value === "string" ? Date.parse(value) : NaN;
  return <span className="overview-event-time">{Number.isFinite(timestamp)
    ? <>发布于 <time dateTime={value!}>{eventTimeFormat.format(timestamp)}</time></>
    : "发布时间未提供"}</span>;
}

export function validOverviewBriefs(value: unknown): value is Briefs {
  return validAuditDetailPayload("briefs", value) && (value as Briefs).daily_news_briefs.every(row =>
    /^\d{4}-\d{2}-\d{2}$/.test(row.brief_date) && Number.isInteger(row.revision_number));
}

export function validOverviewEvents(value: unknown): value is Events {
  if (!value || typeof value !== "object" || "error" in value) return false;
  const body = value as Record<string, unknown>;
  return (body.mode === "eligible" && typeof body.snapshot_id === "string") && Array.isArray(body.items)
    && body.items.every(row => row && typeof row.event_key === "string"
      && typeof row.canonical_headline === "string" && row.broad_model_eligible === true);
}

function useOverviewResource<T>(url: string, validate: (value: unknown) => value is T, snapshot: boolean) {
  const [state, setState] = useState<ReadState<T>>({ data: null, error: null });
  const refresh = useCallback((force = false) => {
    void loadDashboardResource<T>(url, { force, maxAgeMs: INTERVAL, validate }).catch(() => {});
  }, [url, validate]);
  useEffect(() => {
    const sync = () => {
      const current = readDashboardResourceState<T>(url);
      setState({ data: current.data && validate(current.data) ? current.data : null, error: current.error });
    };
    const unsubscribe = subscribeDashboardResource(url, sync);
    sync();
    const stop = scheduleDashboardRefresh(() => refresh(), () => refresh(true), INTERVAL,
      snapshot ? "build-snapshot" : "current", url);
    return () => { unsubscribe(); stop(); };
  }, [url, validate, snapshot, refresh]);
  return { ...state, retry: () => refresh(true) };
}

function ReadNotice({ state, retry }: { state: ReadState<unknown>; retry?: () => void }) {
  if (state.error) return <div className="overview-read-notice" role="status">
    <span>{state.data ? "更新失败 · 显示上次内容" : "暂时无法读取"}</span>
    <button type="button" onClick={retry}>重试</button>
  </div>;
  if (!state.data) return <p className="overview-empty" role="status">正在读取…</p>;
  return null;
}

export function OverviewCards({ briefs, events, snapshot = false, retryBriefs, retryEvents }: {
  briefs: ReadState<Briefs>; events: ReadState<Events>; snapshot?: boolean;
  retryBriefs?: () => void; retryEvents?: () => void;
}) {
  const latest = briefs.data?.daily_news_briefs.slice().sort((a, b) =>
    b.brief_date.localeCompare(a.brief_date) || b.revision_number - a.revision_number)[0];
  const headlines = latest?.brief.items.slice(0, 3) ?? [];
  const rows = events.data?.items.slice(0, 3) ?? [];
  return <div className="overview-news">
    <section className="overview-news-card overview-brief" aria-label="每日简报">
      <header><h2><ReaderIcon aria-hidden="true" />每日简报</h2><span>{latest && <time dateTime={latest.brief_date} title={latest.brief_date}>{latest.brief_date.slice(5).replace("-", "/")}</time>}{snapshot ? " · 预览快照" : ""}</span></header>
      <ReadNotice state={briefs} retry={retryBriefs} />
      {headlines.length > 0 ? <ul>{headlines.map((item, index) => <li key={index}><DashboardLink href="/audit?view=briefs" className="overview-headline"><span className="overview-item-copy"><span className="overview-item-title">{publicBriefText(item.headline)}</span>{publicBriefText(item.summary) && <span className="overview-item-summary">{publicBriefText(item.summary)}</span>}</span><ChevronRightIcon aria-hidden="true" /></DashboardLink></li>)}</ul>
        : briefs.data && <p className="overview-empty">简报尚未生成</p>}
      <DashboardLink href="/audit?view=briefs" className="overview-more">查看完整简报 <ArrowRightIcon aria-hidden="true" /></DashboardLink>
    </section>
    <section className="overview-news-card overview-events" aria-label="当前事件">
      <header><h2><BarChartIcon aria-hidden="true" />当前事件</h2>{snapshot && <span>预览快照</span>}</header>
      <ReadNotice state={events} retry={retryEvents} />
      {rows.length > 0 ? <ul>{rows.map(row => <li key={row.event_key}><DashboardLink href="/audit?view=evidence" className="overview-headline"><span className="overview-item-copy"><span className="overview-item-title">{row.canonical_headline}</span><EventTime value={row.source_published_time} /></span><ChevronRightIcon aria-hidden="true" /></DashboardLink></li>)}</ul>
        : events.data && <p className="overview-empty">暂无可用事件</p>}
      <DashboardLink href="/audit?view=evidence" className="overview-more">查看全部事件 <ArrowRightIcon aria-hidden="true" /></DashboardLink>
    </section>
  </div>;
}

export default function OverviewNews({ snapshot }: { snapshot: boolean }) {
  snapshot = snapshot || __AURUM_DEPLOYMENT__.is_preview;
  const briefs = useOverviewResource(BRIEFS, validOverviewBriefs, snapshot);
  const events = useOverviewResource(EVENTS, validOverviewEvents, snapshot);
  return <OverviewCards briefs={briefs} events={events} snapshot={snapshot} retryBriefs={briefs.retry} retryEvents={events.retry} />;
}
