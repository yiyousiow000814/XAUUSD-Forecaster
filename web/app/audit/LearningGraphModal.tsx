"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import CountValue from "../_components/CountValue";
import { formatExactCount } from "../_lib/count-format";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";

type CurvePoint = { decision_time: string; model_version?: string; training_rows?: number; training_dataset_hash?: string; cumulative_quote_return: number; source_gap_before?: boolean };
type Curve = { model_identity: string; source_point_count?: number; chart_point_count?: number; chart_downsampled?: boolean; points: CurvePoint[]; source_point_count_30m?: number; chart_point_count_30m?: number; chart_downsampled_30m?: boolean; points_30m?: CurvePoint[] };
type Candle = { time: string; open: number; high: number; low: number; close: number; ticks?: number };
type MarketData = {
  candles: Candle[]; overview_candles?: Candle[]; decisions: Decision[];
  training_markers: TrainingMarker[]; decision_resource?: string; history_resource?: string;
  history_start?: string | null; history_end?: string | null; detail_start?: string | null;
  source_candle_count?: number; overview_downsampled?: boolean;
  prediction_history_start?: Record<string, string>;
  mode?: "detail" | "overview"; preview_limited?: boolean;
  source_decision_count?: number; decision_downsampled?: boolean;
  page?: { start?: string; end?: string; has_earlier: boolean; has_later: boolean };
};
type Decision = {
  source_decision_id: string; decision_time: string; exit_time?: string;
  model_identity: string; model_version: string; recommended_action: string; prediction_status?: string;
  policy_consistent?: boolean; policy_expected_action?: string; frozen_record?: boolean;
  outcome_status: string; value_quote_return: number | null;
  outcome_reason_codes?: string[];
  long_quote_return: number | null; short_quote_return: number | null;
  predicted_direction_u5: number | null; ev_long_u5: number | null;
  ev_short_u5: number | null; lcb_long_u5: number | null; lcb_short_u5: number | null;
};
type TrainingMarker = { model_identity: string; training_dataset_hash: string; created_at: string; training_rows: number; artifact_count: number };
type BoundaryReadout = {
  decision_time: string; direction: number | null; news: number | null;
  changes: Array<{ model_identity: string; model_version: string; training_rows?: number }>;
  event_count?: number;
};
type VersionGroup = {
  model_identity: string; training_dataset_hash: string; generation: number;
  lifecycle_status: "LATEST" | "PREVIOUS" | "ARCHIVED"; created_at: string;
  latest_rebuild_at: string; training_rows: number; artifact_rebuilds: number;
  model_versions: string[]; subsequent_oos_rows: number; distinct_days: number;
  cumulative_quote_return: number; profit_factor_quote_adjusted: number | null;
  coverage_rate: number | null; average_oracle_regret: number | null;
  cadence_metrics?: Record<EvaluationCadence, CadenceMetric>;
};
type EvaluationCadence = "EVERY_5M" | "FIXED_30M";
type CadenceMetric = { oos_rows: number; distinct_days: number; cumulative_quote_return: number; profit_factor_quote_adjusted: number | null; coverage_rate: number | null };
type ExecutionModel = {
  model_identity: string; training_rows: number; training_decisions?: number;
  training_observations?: number; predictions: number; scores: number;
  action_counts?: Record<string, number>;
  evaluation: {
    score_count: number; selected_cumulative_return?: number;
    baseline_cumulative_return?: number; delta_cumulative_return?: number; unit: string;
    chart_source_count?: number; chart_point_count?: number; chart_downsampled?: boolean;
    points: Array<Record<string, string | number>>;
    results?: Array<Record<string, string | number>>;
  };
};
type ExecutionLearning = { models: ExecutionModel[]; shadow_only: boolean; source_model_label?: string; training_contract?: string };
type ExecutionHistoryResponse = {
  items: Array<Record<string, string | number>>; total: number;
  next_cursor: string | null; preview_limited?: boolean;
};
type GraphTab = "curve" | "versions" | "market" | "execution";
type HistoryResponse<T> = { items: T[]; preview_limited?: boolean };

const HISTORY_CACHE_MAX_AGE_MS = 60_000;
const historyCacheAge = (payload: unknown) => payload && typeof payload === "object"
  && (payload as { preview_limited?: unknown }).preview_limited === true
  ? Number.POSITIVE_INFINITY
  : HISTORY_CACHE_MAX_AGE_MS;

function curveResponseItems(
  body: HistoryResponse<Array<(CurvePoint & { model_identity: string }) | (Curve & { cadence?: string })>[number]>,
  cadence: EvaluationCadence,
): Curve[] {
  const summaries = body.items.filter(item => Array.isArray((item as Curve).points)) as Array<Curve & { cadence?: string }>;
  const flatPoints = body.items.filter(item => !Array.isArray((item as Curve).points)) as Array<CurvePoint & { model_identity: string }>;
  const identities = [...new Set(flatPoints.map(point => point.model_identity))];
  return summaries.length ? summaries.map(summary => cadence === "FIXED_30M"
    ? {
        ...summary, points: [], points_30m: summary.points,
        source_point_count_30m: summary.source_point_count,
        chart_point_count_30m: summary.chart_point_count,
        chart_downsampled_30m: summary.chart_downsampled,
      }
    : summary) : identities.map(modelIdentity => {
    const points = flatPoints.filter(point => point.model_identity === modelIdentity)
      .map(point => ({
        decision_time: point.decision_time,
        model_version: point.model_version,
        training_rows: point.training_rows,
        training_dataset_hash: point.training_dataset_hash,
        cumulative_quote_return: point.cumulative_quote_return,
        source_gap_before: point.source_gap_before,
      }))
      .sort((a, b) => Date.parse(a.decision_time) - Date.parse(b.decision_time));
    return cadence === "FIXED_30M"
      ? { model_identity: modelIdentity, points: [], points_30m: points }
      : { model_identity: modelIdentity, points };
  });
}

const LABELS: Record<string, string> = {
  CHAMPION_0: "零收益基准", MARKET_ONLY: "黄金自身", NEWS_RESIDUAL: "核心新闻修正量",
  FULL: "黄金＋核心新闻", BROAD_NEWS_RESIDUAL: "大视野新闻修正量", BROAD_FULL: "黄金＋大视野新闻",
  NEWS_ONLY: "纯新闻方向",
};
const COLORS: Record<string, string> = {
  MARKET_ONLY: "#8c5b16", NEWS_RESIDUAL: "#4169a1", FULL: "#476b19",
  BROAD_NEWS_RESIDUAL: "#7651a8", BROAD_FULL: "#c9362b", CHAMPION_0: "#777267",
  NEWS_ONLY: "#d08a00",
};
const pct = (value: number) => `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(3)}%`;
export default function LearningGraphModal({
  open, onClose, startTab, curves, market, versionGroups, execution, historyResource,
}: {
  open: boolean; onClose: () => void; startTab?: "curve" | "execution"; curves: Curve[];
  market?: MarketData;
  versionGroups: VersionGroup[]; execution?: ExecutionLearning; historyResource?: string;
}) {
  const [tab, setTab] = useState<GraphTab>(startTab ?? "curve");
  const [identity, setIdentity] = useState("BROAD_FULL");
  const [remoteMarket, setRemoteMarket] = useState<typeof market>(() => market?.decision_resource
    ? readDashboardResource<typeof market>(market.decision_resource) ?? undefined
    : undefined);
  const dialogRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus());
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      )].filter(element => !element.hasAttribute("disabled"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.body.classList.add("modal-open");
    window.addEventListener("keydown", close);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.body.classList.remove("modal-open");
      window.removeEventListener("keydown", close);
      openerRef.current?.focus();
    };
  }, [open]);
  useEffect(() => {
    if (!open || !market?.decision_resource || market.history_resource) return;
    let cancelled = false;
    const cached = readDashboardResource<typeof market>(market.decision_resource);
    loadDashboardResource<typeof market>(market.decision_resource, {
      maxAgeMs: historyCacheAge(cached),
    })
      .then(body => { if (!cancelled) setRemoteMarket(body); })
      .catch(() => { /* The status snapshot remains a safe empty fallback. */ });
    return () => { cancelled = true; };
  }, [open, market]);
  const resolvedMarket = market?.decision_resource ? remoteMarket ?? market : market;
  if (!open) return null;
  return <div className="graph-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} className={`graph-modal graph-modal-${tab}`} role="dialog" aria-modal="true" aria-labelledby="graph-modal-title">
      <header><div><span>SHADOW EVIDENCE VISUALIZER</span><h2 id="graph-modal-title">模型与 XAUUSD 时间轴</h2></div><button ref={closeButtonRef} type="button" onClick={onClose} aria-label="关闭图表">×</button></header>
      <nav aria-label="图表类型">
        <button className={tab === "curve" ? "active" : ""} onClick={() => setTab("curve")}>长期 OOS 曲线</button>
        <button className={tab === "versions" ? "active" : ""} onClick={() => setTab("versions")}>每组独立成绩</button>
        <button className={tab === "market" ? "active" : ""} onClick={() => setTab("market")}>K线与决策</button>
        <button className={tab === "execution" ? "active" : ""} onClick={() => setTab("execution")}>仓位与退出</button>
      </nav>
      <div className="graph-modal-body">
        {tab === "curve" && <LongCurve curves={curves} historyResource={historyResource} />}
        {tab === "versions" && <VersionLedger groups={versionGroups} historyResource={historyResource} />}
        {tab === "market" && <MarketChart market={resolvedMarket} identity={identity} setIdentity={setIdentity} />}
        {tab === "execution" && <ExecutionCharts execution={execution} historyResource={historyResource} />}
      </div>
      <footer><b>统一口径：</b> 所有曲线只使用模型创建后真正没见过的 30 分钟结果；WAIT 显示为灰色双向箭头，但收益固定为零，不会被画成一笔虚构交易。</footer>
    </section>
  </div>;
}

function VersionLedger({ groups, historyResource }: { groups: VersionGroup[]; historyResource?: string }) {
  const pageSize = 6;
  const [identity, setIdentity] = useState("BROAD_FULL");
  const [cadence, setCadence] = useState<EvaluationCadence>("EVERY_5M");
  const [cutoffWindow, setCutoffWindow] = useState<"20" | "all">("20");
  const [hovered, setHovered] = useState<VersionGroup | null>(null);
  const [page, setPage] = useState(0);
  const overviewUrl = historyResource ? `${historyResource}?resource=version-overview` : "";
  const cachedOverview = overviewUrl
    ? readDashboardResource<HistoryResponse<VersionGroup>>(overviewUrl) : null;
  const initialPageUrl = historyResource
    ? `${historyResource}?resource=version-group&identity=BROAD_FULL&limit=${pageSize}` : "";
  const cachedInitialPage = initialPageUrl
    ? readDashboardResource<{ items: VersionGroup[]; total: number; next_cursor: string | null; preview_limited?: boolean }>(initialPageUrl) : null;
  const [remotePages, setRemotePages] = useState<Record<number, VersionGroup[]>>(
    cachedInitialPage ? { 0: cachedInitialPage.items } : {},
  );
  const [pageCursors, setPageCursors] = useState<Record<number, string | null>>(
    cachedInitialPage ? { 0: null, 1: cachedInitialPage.next_cursor } : { 0: null },
  );
  const [remoteTotal, setRemoteTotal] = useState<number | null>(cachedInitialPage?.total ?? null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [overviewGroups, setOverviewGroups] = useState<VersionGroup[] | null>(cachedOverview?.items ?? null);
  const [overviewState, setOverviewState] = useState<"loading" | "ready" | "error">(
    historyResource && !cachedOverview ? "loading" : "ready",
  );
  const [overviewRetry, setOverviewRetry] = useState(0);
  const [pageRetry, setPageRetry] = useState(0);
  const resultListRef = useRef<HTMLDivElement>(null);
  const pendingPageScrollRef = useRef(false);
  const rows = groups.filter(row => row.model_identity === identity).sort((a,b) => b.generation-a.generation);
  const pageCursor = pageCursors[page];
  useEffect(() => {
    if (!historyResource) return;
    const url = `${historyResource}?resource=version-overview`;
    const cached = readDashboardResource<HistoryResponse<VersionGroup>>(url);
    let cancelled = false;
    loadDashboardResource<HistoryResponse<VersionGroup>>(url, {
      force: overviewRetry > 0,
      maxAgeMs: historyCacheAge(cached),
    }).then(body => {
      if (!cancelled) {
        setOverviewGroups(body.items);
        setOverviewState("ready");
      }
    }).catch(() => {
      if (cancelled) return;
      if (!cancelled && !cached) setOverviewState("error");
    });
    return () => { cancelled = true; };
  }, [historyResource, overviewRetry]);
  useEffect(() => {
    if (!historyResource || pageCursor === undefined) return;
    const query = new URLSearchParams({
      resource: "version-group", identity, limit: String(pageSize),
    });
    if (pageCursor) query.set("cursor", pageCursor);
    const url = `${historyResource}?${query}`;
    const cached = readDashboardResource<{ items: VersionGroup[]; total: number; next_cursor: string | null; preview_limited?: boolean }>(url);
    let cancelled = false;
    loadDashboardResource<{ items: VersionGroup[]; total: number; next_cursor: string | null; preview_limited?: boolean }>(url, {
      force: pageRetry > 0,
      maxAgeMs: historyCacheAge(cached),
    })
      .then(body => {
        if (cancelled) return;
        setRemotePages(previous => ({ ...previous, [page]: body.items }));
        setRemoteTotal(body.total);
        setPageCursors(previous => ({ ...previous, [page + 1]: body.next_cursor }));
      })
      .catch(() => {
        if (cancelled) return;
        if (!cancelled && !cached) setPageError("训练记录读取失败");
      });
    return () => { cancelled = true; };
  }, [historyResource, identity, page, pageCursor, pageRetry]);
  const totalRows = remoteTotal ?? rows.length;
  const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const visibleRows = remotePages[safePage]
    ?? rows.slice(safePage * pageSize, (safePage + 1) * pageSize);
  const pageLoading = Boolean(historyResource && !remotePages[safePage] && !pageError);
  const goToPage = (nextPage: number) => {
    pendingPageScrollRef.current = true;
    setPage(Math.max(0, Math.min(pageCount - 1, nextPage)));
  };
  useEffect(() => {
    if (!pendingPageScrollRef.current || pageLoading || pageError) return;
    pendingPageScrollRef.current = false;
    const frame = window.requestAnimationFrame(() => {
      const anchor = resultListRef.current;
      const scroller = anchor?.closest<HTMLElement>(".graph-modal-body");
      if (!anchor || !scroller) return;
      const top = scroller.scrollTop + anchor.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
      scroller.scrollTo({
        top,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pageLoading, pageError, safePage]);
  const stamp = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12:false, month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" });
  const metric = (row: VersionGroup) => row.cadence_metrics?.[cadence] ?? { oos_rows: row.subsequent_oos_rows, distinct_days: row.distinct_days, cumulative_quote_return: row.cumulative_quote_return, profit_factor_quote_adjusted: row.profit_factor_quote_adjusted, coverage_rate: row.coverage_rate };
  const graphGroups = overviewGroups ?? groups;
  const matureRows = graphGroups.filter(row => metric(row).oos_rows > 0);
  const fullCutoffByCreatedAt = new Map(graphGroups.filter(row => row.model_identity === "FULL" || row.model_identity === "BROAD_FULL").map(row => [row.created_at, row.training_rows]));
  const comparisonCutoff = (row: VersionGroup) => row.model_identity.endsWith("NEWS_RESIDUAL") ? fullCutoffByCreatedAt.get(row.created_at) ?? row.training_rows : row.training_rows;
  const allCutoffs = [...new Set(matureRows.map(comparisonCutoff))].sort((a, b) => a - b);
  const cutoffs = cutoffWindow === "all" ? allCutoffs : allCutoffs.slice(-20);
  const graphRows = matureRows.filter(row => cutoffs.includes(comparisonCutoff(row)));
  const values = graphRows.map(row => metric(row).cumulative_quote_return).concat(0);
  const low = Math.min(...values); const high = Math.max(...values);
  const gx = (trainingRows: number) => cutoffs.length === 1
    ? 480
    : 90 + cutoffs.indexOf(trainingRows) / Math.max(1, cutoffs.length - 1) * 780;
  const gy = (value: number) => 28 + (high-value)/Math.max(.000001,high-low)*218;
  const hoveredMetric = hovered ? metric(hovered) : null;
  return <section className="version-ledger modal-version-ledger"><header><div className="version-ledger-title"><span>共同训练截止量对齐 · 同一坐标叠加比较</span><h3>所有模型的训练组成绩</h3></div><div className="version-ledger-controls"><label className="version-ledger-model"><span>查看模型明细</span><select value={identity} onChange={event => { setIdentity(event.target.value); setPage(0); setRemotePages({}); setPageCursors({ 0: null }); setRemoteTotal(null); setPageError(null); setPageRetry(0); }}>{Object.entries(LABELS).filter(([key]) => key !== "CHAMPION_0").map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label><label><span>统计频率</span><select value={cadence} onChange={event => { setCadence(event.target.value as EvaluationCadence); setPage(0); }}><option value="EVERY_5M">每5分钟（重叠样本）</option><option value="FIXED_30M">每30分钟（固定 :00 / :30）</option></select></label><label><span>横轴范围</span><select value={cutoffWindow} onChange={event => setCutoffWindow(event.target.value as "20" | "all")}><option value="20">最近20个训练截止点</option><option value="all">全部训练截止点</option></select></label></div></header>
    <section className="version-hover-chart" aria-label="所有模型训练组独立收益图">
      <div className="version-hover-readout">{hovered && hoveredMetric ? <><b>{LABELS[hovered.model_identity]} · 第 {hovered.generation} 组</b><span>{stamp(hovered.created_at)} · 共同截止 {formatExactCount(comparisonCutoff(hovered))} 条 · 自身训练 {formatExactCount(hovered.training_rows)} 条 · OOS {formatExactCount(hoveredMetric.oos_rows)} 条 · 收益 {pct(hoveredMetric.cumulative_quote_return)} · PF {hoveredMetric.profit_factor_quote_adjusted?.toFixed(2) ?? "—"} · 出方向 {((hoveredMetric.coverage_rate ?? 0)*100).toFixed(1)}%</span></> : <><b>模型成绩对比</b><span>按同一训练截止点比较。</span></>}</div>
      {overviewState === "loading" ? <GraphLoading label="正在更新训练总览" compact /> : overviewState === "error" ? <GraphLoadError compact label="训练总览读取失败" onRetry={() => {
        setOverviewState("loading");
        setOverviewRetry(value => value + 1);
      }} /> : graphRows.length ? <svg viewBox="0 0 960 300" role="img">
        <line x1="70" x2="890" y1={gy(0)} y2={gy(0)} className="zero-line" />
        <text x="12" y={gy(high)+4}>{pct(high)}</text><text x="12" y={gy(low)+4}>{pct(low)}</text>
        {cutoffs.map(trainingRows => <g key={trainingRows} className="generation-axis"><line x1={gx(trainingRows)} x2={gx(trainingRows)} y1="252" y2="258" /><text x={gx(trainingRows)} y="279" textAnchor="middle">{trainingRows} 条</text></g>)}
        {Object.keys(LABELS).filter(key => key !== "CHAMPION_0").map(key => {
          const modelRows = graphRows.filter(row => row.model_identity === key).sort((a,b) => comparisonCutoff(a)-comparisonCutoff(b));
          const selected = key === identity;
          return <g key={key} opacity={selected ? 1 : .48}>{modelRows.slice(1).map((row, index) => {
            const previous = modelRows[index];
            const previousIndex = cutoffs.indexOf(comparisonCutoff(previous));
            const currentIndex = cutoffs.indexOf(comparisonCutoff(row));
            const crossesMissingCutoff = currentIndex !== previousIndex + 1;
            return <line key={`${previous.training_dataset_hash}-${row.training_dataset_hash}`} x1={gx(comparisonCutoff(previous))} y1={gy(metric(previous).cumulative_quote_return)} x2={gx(comparisonCutoff(row))} y2={gy(metric(row).cumulative_quote_return)} stroke={COLORS[key]} strokeWidth={selected ? "3.5" : "2.25"} strokeDasharray={crossesMissingCutoff ? "7 6" : undefined} />;
          })}{modelRows.map(row => <circle key={row.training_dataset_hash} cx={gx(comparisonCutoff(row))} cy={gy(metric(row).cumulative_quote_return)} r={selected ? "6" : "5"} fill={COLORS[key]} stroke="#eee9dc" strokeWidth="2" tabIndex={0} onMouseEnter={() => setHovered(row)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(row)} onBlur={() => setHovered(null)}><title>{`${LABELS[key]} · 共同截止 ${formatExactCount(comparisonCutoff(row))} 条 · 自身训练 ${formatExactCount(row.training_rows)} 条 · 自身第 ${row.generation} 组 · ${pct(metric(row).cumulative_quote_return)}`}</title></circle>)}</g>;
        })}
      </svg> : <Empty title="暂无训练组结果" text="这个频率还没有成熟的训练组结果。" />}
      <div className="chart-legend">{Object.entries(LABELS).filter(([key]) => key !== "CHAMPION_0").map(([key,label]) => <span key={key}><i style={{ background:COLORS[key] }} />{label}</span>)}</div>
    </section>
    <div ref={resultListRef} className="version-list-anchor" aria-hidden="true" />
    <div className="version-page-stage" aria-busy={pageLoading}>
    {pageLoading ? <GraphLoading label="正在读取这组成绩" compact /> : pageError ? <GraphLoadError compact label={pageError} onRetry={() => {
      setPageError(null);
      setPageRetry(value => value + 1);
    }} /> : <>
    {pageCount > 1 && <VersionPagination page={safePage} pageCount={pageCount} total={totalRows} onPage={goToPage} />}
    <div className="version-ledger-head"><span>组别 / 状态</span><span>训练与上线</span><span>创建后 OOS</span><span>本组独立收益</span><span>PF / 出方向</span></div>
    {visibleRows.map(row => { const selected = metric(row); return <article key={`${row.model_identity}-${row.training_dataset_hash}`} className={row.lifecycle_status === "LATEST" ? "is-latest" : ""}>
      <div className="version-result-head">
        <span className="version-group"><b>第 {row.generation} 组</b><small>{row.lifecycle_status === "LATEST" ? "最新版" : row.lifecycle_status === "PREVIOUS" ? "前一版" : "已归档"}</small></span>
        <span className="version-training"><b><CountValue value={row.training_rows} suffix=" 条" /></b><small>{stamp(row.created_at)}{row.artifact_rebuilds ? ` · 恢复重建 ${formatExactCount(row.artifact_rebuilds)} 次` : ""}</small></span>
      </div>
      <div className="version-result-metrics">
        <span data-label="上线后"><b><CountValue value={selected.oos_rows} suffix=" 条" /></b><small>{formatExactCount(selected.distinct_days)} 个日期</small></span>
        <strong data-label="本组收益">{selected.oos_rows ? pct(selected.cumulative_quote_return) : "等待结果"}</strong>
        <span data-label="PF / 出方向"><b>{selected.profit_factor_quote_adjusted?.toFixed(2) ?? "—"}</b><small>出方向 {((selected.coverage_rate ?? 0)*100).toFixed(1)}%</small></span>
      </div>
    </article>})}
    {pageCount > 1 && <VersionPagination page={safePage} pageCount={pageCount} total={totalRows} onPage={goToPage} position="bottom" />}
    {!totalRows && <p>这个模型还没有真实训练版本。</p>}
    </>}
    </div>
  </section>;
}

function VersionPagination({ page, pageCount, total, onPage, position = "top" }: {
  page: number; pageCount: number; total: number;
  onPage: (page: number) => void; position?: "top" | "bottom";
}) {
  return <nav className={`version-pagination version-pagination-${position}`} aria-label={`训练组分页（${position === "top" ? "顶部" : "底部"}）`}>
    <button type="button" aria-label="上一页训练组" disabled={page === 0} onClick={() => onPage(page - 1)}>←</button>
    <span><b>{formatExactCount(page + 1)}</b> / {formatExactCount(pageCount)}<small><CountValue value={total} suffix=" 组" /></small></span>
    <button type="button" aria-label="下一页训练组" disabled={page >= pageCount - 1} onClick={() => onPage(page + 1)}>→</button>
  </nav>;
}

function LongCurve({ curves, historyResource }: { curves: Curve[]; historyResource?: string }) {
  const [range, setRange] = useState<"24h" | "7d" | "30d" | "all">("24h");
  const [cadence, setCadence] = useState<EvaluationCadence>("EVERY_5M");
  const [pageOffset, setPageOffset] = useState(0);
  const [hoveredBoundary, setHoveredBoundary] = useState<BoundaryReadout | null>(null);
  const initialHistoryUrl = historyResource
    ? `${historyResource}?resource=curve-overview&cadence=5m` : "";
  const initialHistory = initialHistoryUrl
    ? readDashboardResource<HistoryResponse<(CurvePoint & { model_identity: string }) | (Curve & { cadence?: string })>>(initialHistoryUrl) : null;
  const [historyCurves, setHistoryCurves] = useState<Partial<Record<EvaluationCadence, Curve[]>>>(
    initialHistory ? { EVERY_5M: curveResponseItems(initialHistory, "EVERY_5M") } : {},
  );
  const [historyErrors, setHistoryErrors] = useState<Partial<Record<EvaluationCadence, boolean>>>({});
  const [historyRetries, setHistoryRetries] = useState<Partial<Record<EvaluationCadence, number>>>({});
  useEffect(() => {
    if (!historyResource || historyErrors[cadence]) return;
    const cadenceQuery = cadence === "FIXED_30M" ? "30m" : "5m";
    const url = `${historyResource}?resource=curve-overview&cadence=${cadenceQuery}`;
    const cached = readDashboardResource<HistoryResponse<(CurvePoint & { model_identity: string }) | (Curve & { cadence?: string })>>(url);
    let cancelled = false;
    loadDashboardResource<HistoryResponse<(CurvePoint & { model_identity: string }) | (Curve & { cadence?: string })>>(url, {
      force: (historyRetries[cadence] ?? 0) > 0,
      maxAgeMs: historyCacheAge(cached),
    }).then(body => {
      if (!cancelled) setHistoryCurves(previous => ({ ...previous, [cadence]: curveResponseItems(body, cadence) }));
    }).catch(() => {
      if (cancelled) return;
      if (!cancelled && !cached) setHistoryErrors(previous => ({ ...previous, [cadence]: true }));
    });
    return () => { cancelled = true; };
  }, [historyResource, cadence, historyErrors, historyRetries]);
  const historyLoading = Boolean(historyResource && !historyCurves[cadence] && !historyErrors[cadence]);
  // The compact learning snapshot and the canonical history overview have
  // different point counts. Never paint the compact fallback while the
  // canonical resource is loading, otherwise the chart visibly redraws with
  // different axes and curves a moment after opening.
  const resolvedCurves = historyResource ? historyCurves[cadence] ?? [] : curves;
  const usable = resolvedCurves.map(row => cadence === "FIXED_30M" ? { ...row, points: row.points_30m ?? [], source_point_count: row.source_point_count_30m, chart_point_count: row.chart_point_count_30m, chart_downsampled: row.chart_downsampled_30m } : row).filter(row => row.model_identity !== "CHAMPION_0" && row.points.length > 0);
  const overviewPoints = usable.flatMap(row => row.points);
  if (!overviewPoints.length) return <div className="chart-block long-curve-block graph-state-shell">
    <div className="chart-caption"><div><b>历史＋实时成熟 OOS（只追加，不重写）</b><span>切换统计频率时，页面结构会保留。</span></div></div>
    <div className="curve-navigation" aria-label="长期 OOS 时间范围">
      <label>统计频率<select value={cadence} onChange={event => { setCadence(event.target.value as EvaluationCadence); setPageOffset(0); }}><option value="EVERY_5M">每5分钟（重叠）</option><option value="FIXED_30M">每30分钟（非重叠）</option></select></label>
      <label>时间窗口<select value={range} onChange={event => { setRange(event.target.value as typeof range); setPageOffset(0); }}><option value="24h">24小时</option><option value="7d">7天</option><option value="30d">30天</option><option value="all">全部总览</option></select></label>
    </div>
    {historyLoading ? <GraphLoading label="正在读取长期曲线" compact /> : historyErrors[cadence] ? <GraphLoadError compact label="长期曲线读取失败" onRetry={() => {
      setHistoryErrors(previous => ({ ...previous, [cadence]: false }));
      setHistoryRetries(previous => ({ ...previous, [cadence]: (previous[cadence] ?? 0) + 1 }));
    }} /> : <Empty compact title="暂无长期曲线" text="第一个预测走完30分钟后才会出现。" />}
  </div>;
  const availableResultTimes = [...new Set(overviewPoints.map(point => Date.parse(point.decision_time)))].sort((a, b) => a - b);
  const fullStart = availableResultTimes[0];
  const fullEnd = availableResultTimes.at(-1)!;
  const rangeMs = range === "24h" ? 24 * 3_600_000 : range === "7d" ? 7 * 86_400_000 : range === "30d" ? 30 * 86_400_000 : Math.max(1, fullEnd-fullStart);
  // Page through windows that contain real matured results. A market closure
  // is not a page of flat scores, so jump directly to the previous result.
  const resultWindows = range === "all" ? [{ start: fullStart, end: fullEnd }] : (() => {
    const windows: Array<{ start: number; end: number }> = [];
    let endIndex = availableResultTimes.length - 1;
    while (endIndex >= 0) {
      const windowEnd = availableResultTimes[endIndex];
      const cutoff = windowEnd - rangeMs;
      let startIndex = endIndex;
      while (startIndex > 0 && availableResultTimes[startIndex - 1] >= cutoff) startIndex -= 1;
      windows.push({ start: availableResultTimes[startIndex], end: windowEnd });
      endIndex = startIndex - 1;
    }
    return windows;
  })();
  const activePage = Math.min(pageOffset, resultWindows.length - 1);
  const { start, end } = resultWindows[activePage];
  const visibleCurves = usable.map(row => {
    const previousPoint = row.points.filter(point => Date.parse(point.decision_time) < start).at(-1);
    const points = row.points.filter(point => {
      const time = Date.parse(point.decision_time);
      return time >= start && time <= end;
    });
    return { ...row, points, previousPoint };
  }).filter(row => row.points.length > 0);
  const visiblePoints = visibleCurves.flatMap(row => row.points);
  const values = visiblePoints.map(point => point.cumulative_quote_return).concat(0);
  const low = Math.min(...values); const high = Math.max(...values);
  const visibleResultTimes = [...new Set(visiblePoints.map(point => Date.parse(point.decision_time)))].sort((a, b) => a - b);
  const expectedStep = cadence === "FIXED_30M" ? 30 * 60_000 : 5 * 60_000;
  const overviewStep = Math.max(45 * 60_000, expectedStep * 3);
  // Plot result time, not wall-clock time. Long closures receive one compact
  // break and never consume the width of the OOS chart.
  const resultPlotUnits = visibleResultTimes.map((time, index) => index === 0 ? 0 : Math.min(
    4,
    Math.max(1, (time - visibleResultTimes[index - 1]) / expectedStep),
  )).reduce<number[]>((units, step, index) => [
    ...units,
    index === 0 ? 0 : units[index - 1] + step,
  ], []);
  const totalResultPlotUnits = Math.max(1, resultPlotUnits.at(-1) ?? 1);
  const resultX = new Map(visibleResultTimes.map((time, index) => [time, 58 + resultPlotUnits[index] / totalResultPlotUnits * 862]));
  const x = (time: string) => resultX.get(Date.parse(time)) ?? 58;
  const tickIndices = Array.from(new Set([0, .25, .5, .75, 1].map(part => Math.round((visibleResultTimes.length - 1) * part))));
  const tickTimes = tickIndices.map(index => new Date(visibleResultTimes[index]).toISOString());
  const curveRuns = (points: CurvePoint[]) => points.reduce<CurvePoint[][]>((runs, point) => {
    const current = runs.at(-1);
    const previous = current?.at(-1);
    // Sparse overview points form a dashed sampled envelope. Dense recent
    // results remain solid, matching the original long-OOS presentation.
    const beginsOverviewBridge = point.source_gap_before === true
      || Boolean(previous && Date.parse(point.decision_time) - Date.parse(previous.decision_time) >= overviewStep);
    if (!current || beginsOverviewBridge) runs.push([point]);
    else current.push(point);
    return runs;
  }, []);
  const axisLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const versionBoundaries = visibleCurves.flatMap(row => row.points.flatMap((point, index) => {
    if (index === 0 || !point.model_version) return [];
    return [{
      decision_time: point.decision_time,
      model_identity: row.model_identity,
      model_version: point.model_version,
      training_rows: point.training_rows,
    }];
  }));
  const rawGroupedBoundaries = [...new Set(versionBoundaries.map(row => row.decision_time))]
    .sort((a, b) => Date.parse(a) - Date.parse(b))
    .map(decisionTime => ({
    decision_time: decisionTime,
    changes: versionBoundaries.filter(row => row.decision_time === decisionTime),
    }));
  const boundaryPools = (changes: typeof versionBoundaries) => {
    const directionRows = changes
      .filter(change => !change.model_identity.endsWith("NEWS_RESIDUAL"))
      .map(change => change.training_rows ?? 0);
    const newsRows = changes
      .filter(change => change.model_identity.endsWith("NEWS_RESIDUAL"))
      .map(change => change.training_rows ?? 0);
    return {
      direction: directionRows.length ? Math.max(...directionRows) : null,
      news: newsRows.length ? Math.max(...newsRows) : null,
    };
  };
  const boundaryState = rawGroupedBoundaries.reduce<{
    lastDirectionRows: number | null;
    lastNewsRows: number | null;
    boundaries: Array<typeof rawGroupedBoundaries[number] & { direction: number | null; news: number | null }>;
  }>((state, boundary) => {
    const pools = boundaryPools(boundary.changes);
    const direction = pools.direction !== null && pools.direction !== state.lastDirectionRows ? pools.direction : null;
    const news = pools.news !== null && pools.news !== state.lastNewsRows ? pools.news : null;
    return {
      lastDirectionRows: pools.direction ?? state.lastDirectionRows,
      lastNewsRows: pools.news ?? state.lastNewsRows,
      boundaries: direction === null && news === null
        ? state.boundaries
        : [...state.boundaries, { ...boundary, direction, news }],
    };
  }, { lastDirectionRows: null, lastNewsRows: null, boundaries: [] });
  const groupedBoundaries = boundaryState.boundaries;
  const boundaryLabel = (boundary: typeof groupedBoundaries[number]) => [
    boundary.direction !== null ? `方向 ${boundary.direction}` : null,
    boundary.news !== null ? `新闻 ${boundary.news}` : null,
  ].filter(Boolean).join(" / ");
  const compactBoundaryRail = range === "all";
  const markerLimit = compactBoundaryRail ? 36 : 14;
  const displayedBoundaries = groupedBoundaries.length <= markerLimit ? groupedBoundaries : Array.from(
    new Set(Array.from({ length: markerLimit }, (_, index) => Math.round(index * (groupedBoundaries.length - 1) / (markerLimit - 1))))
  ).map(index => groupedBoundaries[index]);
  const boundaryGroups = compactBoundaryRail ? displayedBoundaries.reduce<typeof displayedBoundaries[]>((groups, boundary) => {
    const previous = groups.at(-1);
    const previousBoundary = previous?.at(-1);
    if (previous && previousBoundary && Math.abs(x(boundary.decision_time) - x(previousBoundary.decision_time)) < 15) previous.push(boundary);
    else groups.push([boundary]);
    return groups;
  }, []) : displayedBoundaries.map(boundary => [boundary]);
  const laneEnds: number[] = [];
  const boundaryLayouts = boundaryGroups.map(group => {
    const latest = group.at(-1)!;
    const boundary: BoundaryReadout = {
      decision_time: latest.decision_time,
      direction: [...group].reverse().find(item => item.direction !== null)?.direction ?? null,
      news: [...group].reverse().find(item => item.news !== null)?.news ?? null,
      changes: group.flatMap(item => item.changes),
      event_count: group.length,
    };
    const markerX = group.reduce((total, item) => total + x(item.decision_time), 0) / group.length;
    const label = boundaryLabel(boundary);
    const labelWidth = Math.max(62, Math.min(132, 22 + label.length * 7));
    const idealLabelX = Math.max(58 + labelWidth / 2, Math.min(920 - labelWidth / 2, markerX));
    const idealLeft = idealLabelX - labelWidth / 2;
    let lane = laneEnds.findIndex(endX => idealLeft >= endX + 8);
    if (lane < 0) lane = laneEnds.length;
    laneEnds[lane] = idealLabelX + labelWidth / 2;
    return { boundary, markerX, label, labelWidth, labelX: idealLabelX, lane };
  });
  const boundaryLaneCount = compactBoundaryRail || !boundaryLayouts.length ? 0 : Math.max(...boundaryLayouts.map(layout => layout.lane)) + 1;
  const boundaryDividerY = compactBoundaryRail ? 18 : boundaryLaneCount ? 14 + boundaryLaneCount * 25 : 56;
  const plotTop = compactBoundaryRail ? 46 : boundaryLaneCount ? boundaryDividerY + 14 : 70;
  const plotHeight = Math.max(118, 338 - plotTop);
  const y = (value: number) => plotTop + (high - value) / Math.max(.000001, high - low) * plotHeight;
  const sourcePointCount = usable.reduce((total, row) => total + (row.source_point_count ?? row.points.length), 0);
  const sourceTimeCount = Math.max(...usable.map(row => row.source_point_count ?? row.points.length));
  const chartDownsampled = usable.some(row => row.chart_downsampled);
  const canGoEarlier = range !== "all" && activePage < resultWindows.length - 1;
  const canGoLater = range !== "all" && activePage > 0;
  const windowLabel = `${axisLabel(new Date(start).toISOString())} — ${axisLabel(new Date(end).toISOString())}`;
  return <div className="chart-block long-curve-block">
    <div className="chart-caption"><div><b>历史＋实时成熟 OOS（只追加，不重写）</b><span>数据库永久保留每个成熟结果；图表固定宽度，按时间窗口查看，全部历史只画压缩轮廓。</span></div><strong><CountValue value={sourceTimeCount} suffix=" 个时点" /><small> · <CountValue value={sourcePointCount} suffix=" 条模型评分" /></small></strong></div>
    <div className="curve-navigation" aria-label="长期 OOS 时间范围">
      <label>统计频率<select value={cadence} onChange={event => { setCadence(event.target.value as EvaluationCadence); setPageOffset(0); }}><option value="EVERY_5M">每5分钟（重叠）</option><option value="FIXED_30M">每30分钟（非重叠）</option></select></label>
      <label>时间窗口<select value={range} onChange={event => { setRange(event.target.value as typeof range); setPageOffset(0); }}><option value="24h">24小时</option><option value="7d">7天</option><option value="30d">30天</option><option value="all">全部总览</option></select></label>
      <button type="button" disabled={!canGoEarlier} onClick={() => setPageOffset(activePage + 1)}>← 较早一段</button>
      <button type="button" disabled={!canGoLater} onClick={() => setPageOffset(Math.max(0, activePage - 1))}>较晚一段 →</button>
      <button type="button" disabled={pageOffset === 0} onClick={() => setPageOffset(0)}>回到最新</button>
      <span>{windowLabel}{chartDownsampled ? ` · 全历史 ${formatExactCount(sourcePointCount)} 条已压缩为 ${formatExactCount(overviewPoints.length)} 个绘图点` : ` · 当前 ${formatExactCount(visiblePoints.length)} 个绘图点`}</span>
    </div>
    {historyLoading && <GraphLoading label="正在更新长期曲线" compact />}
    {historyErrors[cadence] && <GraphLoadError compact label="长期曲线更新失败" onRetry={() => {
      setHistoryErrors(previous => ({ ...previous, [cadence]: false }));
      setHistoryRetries(previous => ({ ...previous, [cadence]: (previous[cadence] ?? 0) + 1 }));
    }} />}
    {compactBoundaryRail && <div className="curve-event-readout" aria-live="polite">
      {hoveredBoundary ? <><b>{hoveredBoundary.event_count && hoveredBoundary.event_count > 1 ? `${formatExactCount(hoveredBoundary.event_count)} 次相近换版 · ` : ""}{axisLabel(hoveredBoundary.decision_time)} · {boundaryLabel(hoveredBoundary)}</b><span>{hoveredBoundary.changes.map(change => `${LABELS[change.model_identity] ?? change.model_identity}（${formatExactCount(change.training_rows)} 条）`).join(" · ")}</span></> : <><b>模型换版本事件轨道</b><span>相近换版会合并为一个圆点；移到圆点查看准确时间、方向样本、新闻样本与模型明细。</span></>}
    </div>}
    <span className="mobile-scroll-hint" role="note">左右滑动查看完整图表</span>
    {/* Keyboard users need focus here so arrow keys can pan the wide chart. */}
    {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
    <div className="mobile-chart-scroll" tabIndex={0} aria-label="可左右滑动的长期 OOS 图表">
    <svg className="learning-svg" viewBox="0 0 960 400" role="img" aria-label="各模型历史与实时成熟 OOS 曲线">
      {boundaryLayouts.length > 0 && <line x1="58" x2="920" y1={boundaryDividerY} y2={boundaryDividerY} className={compactBoundaryRail ? "version-event-rail" : "version-label-divider"} />}
      <line x1="58" x2="920" y1={y(0)} y2={y(0)} className="zero-line" />
      <text x="8" y={y(high) + 5}>{pct(high)}</text><text x="8" y={y(low) + 5}>{pct(low)}</text>
      {boundaryLayouts.map(({ boundary, markerX, label, labelWidth, labelX, lane }) => {
        const labelY = 8 + lane * 25;
        return <g key={boundary.decision_time} className="version-boundary">
          <title>{boundary.event_count && boundary.event_count > 1 ? `${formatExactCount(boundary.event_count)} 次相近换版\n` : ""}{boundary.changes.map(change => `${LABELS[change.model_identity] ?? change.model_identity} · 训练 ${formatExactCount(change.training_rows)} 条 · ${change.model_version}`).join("\n")}</title>
          <line className="version-boundary-marker" x1={markerX} x2={markerX} y1={boundaryDividerY} y2="350" />
          {compactBoundaryRail ? <circle className="version-event-dot" cx={markerX} cy={boundaryDividerY} r="5" tabIndex={0} onMouseEnter={() => setHoveredBoundary(boundary)} onMouseLeave={() => setHoveredBoundary(null)} onFocus={() => setHoveredBoundary(boundary)} onBlur={() => setHoveredBoundary(null)} /> : <>
            <path className="version-boundary-leader" d={`M ${labelX} ${labelY + 21} L ${labelX} ${boundaryDividerY - 5} L ${markerX} ${boundaryDividerY}`} />
            <rect className="version-boundary-badge" x={labelX - labelWidth / 2} y={labelY} width={labelWidth} height="21" rx="3" />
            <text x={labelX} textAnchor="middle" y={labelY + 14}>{label}</text>
          </>}
        </g>;
      })}
      {visibleCurves.flatMap(row => {
        const runs = curveRuns(row.points);
        const first = runs[0]?.[0];
        const carryIn = row.previousPoint && first
          && (first.source_gap_before === true
            || Date.parse(first.decision_time) - Date.parse(row.previousPoint.decision_time) >= overviewStep)
          && x(first.decision_time) > 59
          ? <line key={`${row.model_identity}-carry-in`} className="curve-gap-bridge curve-gap-carry-in" stroke={COLORS[row.model_identity]} x1="58" y1={y(row.previousPoint.cumulative_quote_return)} x2={x(first.decision_time)} y2={y(first.cumulative_quote_return)}><title>窗口开始前的压缩历史轮廓</title></line>
          : null;
        return [carryIn, ...runs.flatMap((run, index) => {
          const previous = runs[index - 1]?.at(-1);
          const bridge = previous && run[0]
            ? <line key={`${row.model_identity}-bridge-${index}`} className="curve-gap-bridge" stroke={COLORS[row.model_identity]} x1={x(previous.decision_time)} y1={y(previous.cumulative_quote_return)} x2={x(run[0].decision_time)} y2={y(run[0].cumulative_quote_return)}><title>压缩历史轮廓</title></line>
            : null;
          const curve = run.length === 1
            ? <circle key={`${row.model_identity}-run-${index}`} cx={x(run[0].decision_time)} cy={y(run[0].cumulative_quote_return)} r="4" fill={COLORS[row.model_identity]} />
            : <polyline key={`${row.model_identity}-run-${index}`} fill="none" stroke={COLORS[row.model_identity]} strokeWidth="3" points={run.map(point => `${x(point.decision_time)},${y(point.cumulative_quote_return)}`).join(" ")} />;
          return [bridge, curve];
        })];
      })}
      {tickTimes.map(value => <g key={value} className="time-axis"><line x1={x(value)} x2={x(value)} y1="350" y2="356" /><text x={x(value)} y="374" textAnchor="middle">{axisLabel(value)}</text></g>)}
    </svg>
    </div>
    <div className="chart-legend">{visibleCurves.map(row => <span key={row.model_identity}><i style={{ background: COLORS[row.model_identity] }} />{LABELS[row.model_identity]} <b>{pct(row.points.at(-1)?.cumulative_quote_return ?? 0)}</b></span>)}{groupedBoundaries.length > 0 && <span><i className="train-dot" />模型换版本{compactBoundaryRail ? `（${formatExactCount(boundaryLayouts.length)} 个事件点 / ${formatExactCount(groupedBoundaries.length)} 次）` : groupedBoundaries.length > displayedBoundaries.length ? `（显示 ${formatExactCount(displayedBoundaries.length)}/${formatExactCount(groupedBoundaries.length)}）` : ""}</span>}</div>
  </div>;
}

function MarketChart({ market, identity, setIdentity }: { market?: MarketData; identity: string; setIdentity: (value: string) => void }) {
  const [range, setRange] = useState("24");
  const [page, setPage] = useState(0);
  const [before, setBefore] = useState<string | null>(null);
  const [laterPages, setLaterPages] = useState<Array<string | null>>([]);
  const [historyRetry, setHistoryRetry] = useState(0);
  const [showLong, setShowLong] = useState(true);
  const [showShort, setShowShort] = useState(true);
  const [showWait, setShowWait] = useState(true);
  const [dense, setDense] = useState(false);
  const [showTraining, setShowTraining] = useState(false);
  const [selected, setSelected] = useState<Decision | null>(null);
  const historyQuery = new URLSearchParams({
      range, identity, frequency: dense ? "5m" : "30m",
  });
  if (before && range !== "all") historyQuery.set("before", before);
  const historyQueryString = historyQuery.toString();
  const historyRequestKey = market?.history_resource
    ? `${market.history_resource}?${historyQueryString}` : "";
  const initialHistoryResult = historyRequestKey
    ? readDashboardResource<MarketData>(historyRequestKey) : null;
  const [historyResult, setHistoryResult] = useState<{
    key: string; state: "ready" | "error"; data?: MarketData;
  } | undefined>(() => initialHistoryResult ? {
    key: historyRequestKey,
    state: "ready",
    data: {
      ...initialHistoryResult,
      training_markers: market?.training_markers ?? [],
      prediction_history_start: market?.prediction_history_start,
      history_resource: market?.history_resource,
    },
  } : undefined);
  useEffect(() => {
    if (!market?.history_resource) return;
    let cancelled = false;
    const url = `${market.history_resource}?${historyQueryString}`;
    const cached = readDashboardResource<MarketData>(url);
    loadDashboardResource<MarketData>(url, {
      force: historyRetry > 0,
      maxAgeMs: historyCacheAge(cached),
    })
      .then(body => {
        if (!cancelled) {
          setHistoryResult({
            key: historyRequestKey,
            state: "ready",
            data: {
              ...body,
              training_markers: market.training_markers ?? [],
              prediction_history_start: market.prediction_history_start,
              history_resource: market.history_resource,
            },
          });
        }
      })
      .catch(() => {
        if (!cancelled) {
          if (cancelled) return;
          if (!cached) setHistoryResult({ key: historyRequestKey, state: "error" });
        }
      });
    return () => { cancelled = true; };
  }, [market, historyQueryString, historyRequestKey, historyRetry]);
  const remoteHistory = Boolean(market?.history_resource);
  const historyState = !remoteHistory ? "ready"
    : historyResult?.key !== historyRequestKey ? "loading" : historyResult.state;
  const activeMarket = remoteHistory && historyResult?.key === historyRequestKey
    ? historyResult.data : market;
  const detailCandles = activeMarket?.candles ?? [];
  const allCandles = range === "all" && activeMarket?.overview_candles?.length ? activeMarket.overview_candles : detailCandles;
  const candleCount = range === "all" ? allCandles.length : Number(range) * 12;
  const pageEndIndex = range === "all" ? allCandles.length : Math.max(0, detailCandles.length - page * candleCount);
  const pageStartIndex = range === "all" ? 0 : Math.max(0, pageEndIndex - candleCount);
  const candles = range === "all" ? allCandles : detailCandles.slice(pageStartIndex, pageEndIndex);
  const pageEnd = candles.length ? Date.parse(candles.at(-1)!.time) + 300_000 : 0;
  const cutoff = candles.length ? Date.parse(candles[0].time) : 0;
  const canGoEarlier = range !== "all" && (remoteHistory ? Boolean(activeMarket?.page?.has_earlier) : pageStartIndex > 0);
  const canGoLater = range !== "all" && (remoteHistory ? laterPages.length > 0 : page > 0);
  const goEarlier = () => {
    if (remoteHistory) {
      if (!candles.length) return;
      setLaterPages(value => [...value, before]);
      setBefore(candles[0].time);
    } else {
      setPage(value => value + 1);
    }
  };
  const goLater = () => {
    if (remoteHistory) {
      const previous = laterPages.at(-1) ?? null;
      setLaterPages(value => value.slice(0, -1));
      setBefore(previous);
    } else {
      setPage(value => Math.max(0, value - 1));
    }
  };
  const goLatest = () => { setPage(0); setBefore(null); setLaterPages([]); };
  const scopedDecisions = useMemo(() => (activeMarket?.decisions ?? []).filter(row =>
    row.model_identity === identity && Date.parse(row.decision_time) >= cutoff && Date.parse(row.decision_time) < pageEnd
  ), [activeMarket, identity, cutoff, pageEnd]);
  const arrowAction = (row: Decision) => {
    if (row.ev_long_u5 == null || row.ev_short_u5 == null || row.ev_long_u5 === row.ev_short_u5) return "WAIT";
    const bestAction = row.ev_long_u5 > row.ev_short_u5 ? "LONG" : "SHORT";
    const bestEv = bestAction === "LONG" ? row.ev_long_u5 : row.ev_short_u5;
    return bestEv > 0 ? bestAction : "WAIT";
  };
  const candidateDecisions = scopedDecisions.filter(row =>
    ((arrowAction(row) === "LONG" && showLong) ||
     (arrowAction(row) === "SHORT" && showShort) ||
     (arrowAction(row) === "WAIT" && showWait))
  );
  const decisions = (() => {
    if (dense) return candidateDecisions;
    return candidateDecisions.filter(row => new Date(row.decision_time).getUTCMinutes() % 30 === 0);
  })();
  const low = candles.length ? Math.min(...candles.map(row => row.low)) : 0;
  const high = candles.length ? Math.max(...candles.map(row => row.high)) : 1;
  const candleSteps = candles.slice(1).map((row, index) =>
    Date.parse(row.time) - Date.parse(candles[index].time)
  ).filter(step => step > 0).sort((a, b) => a - b);
  const typicalCandleStep = candleSteps.length ? candleSteps[Math.floor(candleSteps.length / 2)] : 300_000;
  // Plot trading time, not wall-clock time. Long closures get one compact
  // visual break instead of consuming most of the chart width.
  const plotUnits = candles.map((row, index) => index === 0 ? 0 : Math.min(
    4,
    Math.max(1, (Date.parse(row.time) - Date.parse(candles[index - 1].time)) / typicalCandleStep),
  )).reduce<number[]>((values, step, index) => [
    ...values,
    index === 0 ? 0 : values[index - 1] + step,
  ], []);
  const totalPlotUnits = Math.max(1, plotUnits.at(-1) ?? 1);
  const xAtIndex = (index: number) => 55 + plotUnits[index] / totalPlotUnits * 870;
  const marketGaps = candles.slice(1).map((row, index) => {
    const duration = Date.parse(row.time) - Date.parse(candles[index].time) - typicalCandleStep;
    const left = xAtIndex(index);
    const right = xAtIndex(index + 1);
    return { start: left + (right - left) * .25, end: right - (right - left) * .25, duration };
  }).filter(gap => gap.duration >= typicalCandleStep);
  const y = (value: number) => 58 + (high - value) / Math.max(.00001, high - low) * 274;
  const indexByTime = (time: string) => candles.reduce((best, row, index) =>
    Math.abs(Date.parse(row.time) - Date.parse(time)) < Math.abs(Date.parse(candles[best].time) - Date.parse(time)) ? index : best, 0);
  const xTime = (time: string) => xAtIndex(indexByTime(time));
  const byTime = (time: string) => candles[indexByTime(time)];
  const hiddenByAction = scopedDecisions.length - candidateDecisions.length;
  const hiddenByFrequency = candidateDecisions.length - decisions.length;
  const counts = decisions.reduce((total, row) => ({ ...total, [arrowAction(row)]: total[arrowAction(row)] + 1 }), { LONG: 0, SHORT: 0, WAIT: 0 } as Record<string, number>);
  const predictionStart = activeMarket?.prediction_history_start?.[identity];
  const predictionAvailability = predictionStart && pageEnd <= Date.parse(predictionStart)
    ? "模型当时尚未开始预测"
    : "这段时间没有预测";
  const unhealthyWaits = decisions.filter(row => row.recommended_action === "WAIT" && row.prediction_status === "DATA_UNHEALTHY").length;
  const policyMismatchCount = decisions.filter(row => row.policy_consistent === false).length;
  const activeSelected = selected && decisions.some(row => row.source_decision_id === selected.source_decision_id && row.model_identity === selected.model_identity && row.model_version === selected.model_version) ? selected : decisions.at(-1) ?? null;
  const selectedX = activeSelected ? xTime(activeSelected.decision_time) : null;
  const exitTime = (row: Decision) => row.exit_time ?? new Date(Date.parse(row.decision_time) + 30 * 60_000).toISOString();
  const selectedExitX = activeSelected ? Math.min(925, xTime(exitTime(activeSelected))) : null;
  const timeLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const axisTimeLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const timeTickIndices = Array.from(new Set([0, .25, .5, .75, 1].map(part => Math.round((candles.length - 1) * part))));
  const resultLabel = (value: number | null) => value == null ? "等待30分钟结果" : pct(value);
  return <div className="chart-block market-chart-block">
    <div className="chart-caption"><div><b>每根K线5分钟 · 每个箭头预测未来30分钟</b><span>绿色向上、红色向下、灰色双向代表 WAIT。新闻修正量也显示自己的方向：LONG 表示向上修正，SHORT 表示向下修正；完整方向请看“黄金＋新闻”。</span></div><select value={identity} onChange={event => { setIdentity(event.target.value); setSelected(null); }}>{Object.entries(LABELS).filter(([key]) => key !== "CHAMPION_0").map(([key, label]) => <option key={key} value={key}>{label}{key.includes("RESIDUAL") ? "（修正量）" : ""}</option>)}</select></div>
    <div className="market-controls" aria-label="K线图显示控制">
      <label>时间<select value={range} onChange={event => { setRange(event.target.value); setPage(0); setBefore(null); setLaterPages([]); setSelected(null); if (event.target.value === "168") setDense(false); }}><option value="3">3小时</option><option value="6">6小时</option><option value="12">12小时</option><option value="24">24小时</option><option value="168">7天</option><option value="all">全部历史</option></select></label>
      <label>频率<select value={dense ? "all" : "clear"} disabled={range === "168"} onChange={event => setDense(event.target.value === "all")}><option value="clear">每小时 :00 / :30</option><option value="all">每5分钟</option></select></label>
      <button className={showLong ? "active" : ""} type="button" onClick={() => setShowLong(value => !value)}>看多 LONG</button>
      <button className={showShort ? "active" : ""} type="button" onClick={() => setShowShort(value => !value)}>看空 SHORT</button>
      <button className={showWait ? "active" : ""} type="button" onClick={() => setShowWait(value => !value)}>等待 WAIT</button>
      <button className={showTraining ? "active" : ""} type="button" onClick={() => setShowTraining(value => !value)}>模型换版本</button>
      <span>显示 {formatExactCount(decisions.length)}{activeMarket?.decision_downsampled ? ` / 共 ${formatExactCount(activeMarket.source_decision_count ?? decisions.length)}` : ""} 次{hiddenByAction > 0 ? ` · 动作筛选隐藏 ${formatExactCount(hiddenByAction)} 次` : ""}{hiddenByFrequency > 0 ? ` · 频率收起 ${formatExactCount(hiddenByFrequency)} 次` : ""}</span>
    </div>
    {historyState === "loading" && candles.length > 0 && <GraphLoading label="正在更新行情" compact />}
    {historyState === "error" && candles.length > 0 && <GraphLoadError compact label="行情更新失败" onRetry={() => setHistoryRetry(value => value + 1)} />}
    {!candles.length ? <div className="graph-visual-stage market-empty-stage">
      {historyState === "loading" ? <GraphLoading label="正在读取行情" /> : historyState === "error" ? <GraphLoadError label="行情读取失败" onRetry={() => setHistoryRetry(value => value + 1)} /> : canGoLater ? <div className="market-window-empty"><strong>这段时间没有行情</strong><span>已跳过休市或数据空档，可返回较新的交易时段。</span><button type="button" onClick={goLater}>→ 返回较新行情</button></div> : <Empty title="暂无行情数据" text="当前范围没有 Bid/Ask 行情。" />}
    </div> : <>
    <div className="market-history-nav" aria-label="历史行情翻页">
      <button type="button" disabled={!canGoEarlier} onClick={goEarlier} aria-label="查看更早行情">←</button>
      <span>{timeLabel(candles[0].time)} — {timeLabel(candles.at(-1)!.time)}{range === "all" && activeMarket?.overview_downsampled ? ` · 全部 ${formatExactCount(activeMarket.source_candle_count)} 根概览` : ""}</span>
      <button type="button" disabled={!canGoLater} onClick={goLater} aria-label="查看较新行情">→</button>
      {(page > 0 || laterPages.length > 0) && <button type="button" onClick={goLatest}>最新</button>}
    </div>
    <div className="prediction-counts"><b>{scopedDecisions.length ? "成本后EV较高方向" : predictionAvailability}</b>{scopedDecisions.length > 0 && <><span>看多 {formatExactCount(counts.LONG)}</span><span>看空 {formatExactCount(counts.SHORT)}</span><span>等待 {formatExactCount(counts.WAIT)}{unhealthyWaits ? `（数据异常 ${formatExactCount(unhealthyWaits)}）` : ""}</span>{policyMismatchCount > 0 && <span className="negative">历史规则不一致 {formatExactCount(policyMismatchCount)}（原记录保留）</span>}</>}</div>
    <span className="mobile-scroll-hint" role="note">左右滑动查看完整图表</span>
    {/* Keyboard users need focus here so arrow keys can pan the wide chart. */}
    {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
    <div className="mobile-chart-scroll" tabIndex={0} aria-label="可左右滑动的 XAUUSD K线图">
    <svg className="learning-svg" viewBox="0 0 960 400" role="img" aria-label="XAUUSD K线与模型决策">
      {marketGaps.map(gap => <g key={`${gap.start}-${gap.end}`} className="market-gap"><rect x={gap.start} y="52" width={Math.max(2, gap.end-gap.start)} height="280" /><text x={(gap.start+gap.end)/2} y="190" textAnchor="middle">{gap.duration >= 45 * 60_000 ? "休市" : "数据缺口"}</text></g>)}
      {candles.map((row, index) => { const cx = xAtIndex(index); const width = Math.max(1.5, 650 / candles.length); const up = row.close >= row.open; return <g key={row.time}><line x1={cx} x2={cx} y1={y(row.high)} y2={y(row.low)} stroke={up ? "#476b19" : "#c9362b"} /><rect x={cx - width / 2} width={width} y={Math.min(y(row.open), y(row.close))} height={Math.max(1, Math.abs(y(row.open) - y(row.close)))} fill={up ? "#476b19" : "#c9362b"} /></g>; })}
      {selectedX != null && selectedExitX != null && <g className="selected-window"><rect x={selectedX} width={Math.max(2, selectedExitX-selectedX)} y="52" height="280" /><line x1={selectedX} x2={selectedX} y1="52" y2="332" /><line x1={selectedExitX} x2={selectedExitX} y1="52" y2="332" /><text x={selectedX+4} y="49">预测</text><text x={Math.max(selectedX+36, selectedExitX-58)} y="49">30分钟后</text></g>}
      {decisions.map(row => { const candle = byTime(row.decision_time); const cx = xTime(row.decision_time); const action = arrowAction(row); const cy = action === "WAIT" ? 34 : action === "LONG" ? y(candle.low) + 12 : y(candle.high) - 12; const color = action === "LONG" ? "#476b19" : action === "SHORT" ? "#c9362b" : "#555149"; const isSelected = activeSelected?.source_decision_id === row.source_decision_id && activeSelected?.model_identity === row.model_identity && activeSelected?.model_version === row.model_version; return <g key={`${row.source_decision_id}-${row.model_identity}-${row.model_version}`} role="button" tabIndex={0} className={`decision-marker${isSelected ? " selected" : ""}${row.policy_consistent === false ? " policy-mismatch" : ""}`} onClick={() => setSelected(row)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") setSelected(row); }}><title>{`${timeLabel(row.decision_time)} · 成本后EV较高方向 ${action} · 模型版本 ${row.model_version} · 点击查看30分钟结果`}</title>{action === "WAIT" && <circle cx={cx} cy={cy} r="10" fill="#eee9da" stroke={color} strokeWidth="1.5" />}{isSelected && <circle cx={cx} cy={cy} r="14" fill="none" stroke={color} strokeWidth="2" />}{action === "WAIT" ? <path d={`M ${cx-7} ${cy} h 14 M ${cx-7} ${cy} l 4 -4 M ${cx-7} ${cy} l 4 4 M ${cx+7} ${cy} l -4 -4 M ${cx+7} ${cy} l -4 4`} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" /> : <path d={action === "LONG" ? `M ${cx} ${cy-7} l -6 11 h 12 z` : `M ${cx} ${cy+7} l -6 -11 h 12 z`} fill={color} />}</g>; })}
      {showTraining && (activeMarket?.training_markers ?? []).filter(row => row.model_identity === identity && Date.parse(row.created_at) >= cutoff && Date.parse(row.created_at) < pageEnd).map(row => <g key={`${row.model_identity}-${row.training_dataset_hash}`}><title>{`${timeLabel(row.created_at)} · 第一次使用 ${formatExactCount(row.training_rows)} 条训练数据${row.artifact_count > 1 ? ` · 后续恢复重建 ${formatExactCount(row.artifact_count-1)} 次` : ""}`}</title><line x1={xTime(row.created_at)} x2={xTime(row.created_at)} y1="52" y2="332" className="training-line" /><text x={xTime(row.created_at)+4} y="328" className="training-label">{formatExactCount(row.training_rows)}条新训练</text></g>)}
      <text x="5" y="64">{high.toFixed(2)}</text><text x="5" y="335">{low.toFixed(2)}</text>
      {timeTickIndices.map(index => <g key={candles[index].time} className="time-axis"><line x1={xAtIndex(index)} x2={xAtIndex(index)} y1="338" y2="344" /><text x={xAtIndex(index)} y="366" textAnchor="middle">{axisTimeLabel(candles[index].time)}</text></g>)}
    </svg>
    </div>
    <div className="chart-legend"><span><i className="long-dot" />看多预测</span><span><i className="short-dot" />看空预测</span>{showWait && <span><i className="wait-dot" />↔ 等待，不持仓</span>}{showTraining && <span><i className="train-dot" />新训练数据代</span>}</div>
    <div className="decision-reader" aria-live="polite">{activeSelected ? <>
      <div><small>一次完整观察</small><strong>{timeLabel(activeSelected.decision_time)} · 成本后EV较高 {arrowAction(activeSelected)}</strong><span>版本 {activeSelected.model_version} · → {timeLabel(exitTime(activeSelected))} 固定观察结果{activeSelected.policy_consistent === false ? ` · 当时规则校验异常（原记录保留；应为 ${activeSelected.policy_expected_action}）` : ""}</span></div>
      <DecisionPayoff selected={activeSelected} resultLabel={resultLabel} />
    </> : <><div><small>怎样阅读</small><strong>点击图中的三角形</strong><span>这里只显示一次预测；选中后才标出它对应的30分钟观察窗口。</span></div></>}</div>
    </>}
    <p className="wait-explainer"><b>方向怎样产生：</b> Ridge 预测未来30分钟连续收益。系统分别计算 Long 与 Short 的 Bid/Ask 成本后 EV，较高的一边只要大于0就记录为 Shadow 方向；两边都不大于0、数值相同或数据异常才 WAIT。95%下界仍用于观察不确定性和未来晋升，但不再封锁早期 Shadow 方向。U5 只是统一波动尺度，不是 WAIT 开关。</p>
  </div>;
}

function DecisionPayoff({ selected, resultLabel }: { selected: Decision; resultLabel: (value: number | null) => string }) {
  if (selected.outcome_status !== "VALID" && selected.outcome_status !== "PENDING") {
    const codes = selected.outcome_reason_codes ?? [];
    const explanation = codes.some(code => code.includes("CLOCK_AHEAD"))
      ? "旧版 5 秒时钟容差拒绝了这条报价；原始记录保留并隔离，不评分、不训练。当前容差已按实测链路修正为 20 秒。"
      : codes.includes("NO_ENTRY_RECEIVED_WITHIN_EXPIRY")
        ? "预测后20秒有效期内没有收到可执行入场报价；通常来自断线或采集缺口。"
        : codes.includes("NO_EXIT_RECEIVED_AFTER_HORIZON")
          ? "30分钟观察期结束后没有收到可执行退出报价；通常来自断线或采集缺口。"
          : "报价证据不完整；不评分、不训练。";
    return <div><small>30分钟结果</small><strong className="negative">无效样本 · 已隔离</strong><span>{explanation}</span></div>;
  }
  if (selected.outcome_status !== "VALID") return <div><small>30分钟结果</small><strong>等待结算</strong><span>这次预测的固定观察期还没有走完。</span></div>;
  const result = selected.recommended_action === "LONG" ? selected.long_quote_return : selected.recommended_action === "SHORT" ? selected.short_quote_return : 0;
  return <div><small>30分钟结果</small><strong className={(result ?? 0) >= 0 ? "positive" : "negative"}>{selected.recommended_action} {resultLabel(result)}</strong><span>{selected.recommended_action === "WAIT" ? "未持仓，结果固定为零" : (result ?? 0) >= 0 ? "方向正确，成本后为正" : "方向错误，成本后为负"}</span></div>;
}

function ExecutionCharts({ execution, historyResource }: { execution?: ExecutionLearning; historyResource?: string }) {
  const lot = execution?.models.find(row => row.model_identity === "LOT_RIDGE");
  const exit = execution?.models.find(row => row.model_identity === "EXIT_RIDGE");
  if (!lot && !exit) return <Empty title="暂无仓位与退出结果" text="仓位与退出模型还没有生成可评分的前向预测。" />;
  return <section className="execution-charts">
    <header><span>CAUSAL EXECUTION OOS</span><h3>跟随同一个 Live 方向，逐笔看仓位与退出。</h3><p>方向固定来自 {execution?.source_model_label ?? "黄金＋大视野新闻 Ridge"}。WAIT 不创建仓位；历史结果只训练，下面只评分模型上线后真正发生的未来位置。</p></header>
    <div className="execution-scorecards">
      <article><small>仓位倍率 Ridge</small><strong><CountValue value={lot?.evaluation.score_count} suffix=" 笔已评分" /></strong><span>每个方向位置只比较 0.5x / 1.0x / 2.0x</span></article>
      <article><small>Exit Ridge</small><strong><CountValue value={exit?.evaluation.score_count} suffix=" 笔已评分" /></strong><span>{formatExactCount(exit?.predictions)} 次途中检查 · 提前退出 {formatExactCount(exit?.action_counts?.EXIT)} 次 · 继续持有 {formatExactCount(exit?.action_counts?.HOLD)} 次</span></article>
    </div>
    {(exit?.predictions ?? 0) > 0 && (exit?.action_counts?.EXIT ?? 0) === 0 && <p className="execution-callout"><b>目前没有提前退出。</b> Exit Ridge 已正常检查，但每次预测的“从当前继续持有到30分钟”的收益都大于零，因此全部选择继续持有。这是当前模型结果，不是页面遗漏。</p>}
    <div className="execution-chart-grid">
      <ExecutionHistoryChart title="仓位倍率" subtitle="模型选择 vs 固定 1.0x" model={lot} historyResource={historyResource} firstKey="selected_cumulative_return" secondKey="baseline_cumulative_return" firstLabel="Ridge 倍率" secondLabel="固定 1.0x" />
      <ExecutionHistoryChart title="退出动作" subtitle="顺序 Exit Ridge vs 固定持有30分钟" model={exit} historyResource={historyResource} firstKey="selected_cumulative_return" secondKey="baseline_cumulative_return" firstLabel="顺序 Exit Ridge" secondLabel="固定30分钟" />
    </div>
    <ExecutionResultLists lot={lot} exit={exit} />
  </section>;
}

function ExecutionHistoryChart({ title, subtitle, model, historyResource, firstKey, secondKey, firstLabel, secondLabel }: {
  title: string; subtitle: string; model?: ExecutionModel; historyResource?: string;
  firstKey: string; secondKey: string; firstLabel: string; secondLabel: string;
}) {
  const pageSize = 96;
  const identity = model?.model_identity ?? "";
  const firstUrl = historyResource && identity
    ? `${historyResource}?resource=execution-point&identity=${encodeURIComponent(identity)}&limit=${pageSize}` : "";
  const initial = firstUrl ? readDashboardResource<ExecutionHistoryResponse>(firstUrl) : null;
  const [page, setPage] = useState(0);
  const [pages, setPages] = useState<Record<number, Array<Record<string, string | number>>>>(
    initial ? { 0: initial.items } : {},
  );
  const [cursors, setCursors] = useState<Record<number, string | null>>(
    initial ? { 0: null, 1: initial.next_cursor } : { 0: null },
  );
  const [total, setTotal] = useState(initial?.total ?? model?.evaluation.chart_source_count ?? 0);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const cursor = cursors[page];
  useEffect(() => {
    if (!firstUrl || cursor === undefined || pages[page]) return;
    let cancelled = false;
    const url = cursor ? `${firstUrl}&cursor=${encodeURIComponent(cursor)}` : firstUrl;
    const cached = readDashboardResource<ExecutionHistoryResponse>(url);
    loadDashboardResource<ExecutionHistoryResponse>(url, {
      force: retry > 0,
      maxAgeMs: historyCacheAge(cached),
    }).then(body => {
      if (cancelled) return;
      setPages(previous => ({ ...previous, [page]: body.items }));
      setCursors(previous => ({ ...previous, [page + 1]: body.next_cursor }));
      setTotal(body.total);
    }).catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [cursor, firstUrl, page, pages, retry]);
  const remotePoints = pages[page];
  const fallbackPoints = page === 0 ? model?.evaluation.points ?? [] : [];
  const points = (remotePoints ?? fallbackPoints).slice().sort((a, b) => Date.parse(String(a.time)) - Date.parse(String(b.time)));
  const loading = Boolean(firstUrl && !remotePoints && !error);
  const hasEarlier = typeof cursors[page + 1] === "string";
  const label = points.length
    ? `${new Date(String(points[0].time)).toLocaleString("zh-CN", { month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false })} — ${new Date(String(points.at(-1)!.time)).toLocaleString("zh-CN", { month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false })}`
    : "暂无时间范围";
  const controls = firstUrl ? <div className="execution-history-nav" aria-label={`${title}历史时间窗口`}>
    <button type="button" disabled={!hasEarlier} onClick={() => setPage(value => value + 1)}>← 较早</button>
    <span>{label}<small>第 {formatExactCount(page + 1)} 段 · 共 {formatExactCount(total)} 个历史绘图点</small></span>
    <button type="button" disabled={page === 0} onClick={() => setPage(value => Math.max(0, value - 1))}>较晚 →</button>
    {page > 0 && <button type="button" onClick={() => setPage(0)}>最新</button>}
  </div> : undefined;
  return <ExecutionLineChart title={title} subtitle={subtitle} points={points}
    sourceCount={total || model?.evaluation.chart_source_count} downsampled={model?.evaluation.chart_downsampled}
    firstKey={firstKey} secondKey={secondKey} firstLabel={firstLabel} secondLabel={secondLabel}
    format={pct} controls={controls} loading={loading} error={error ? () => { setError(false); setRetry(value => value + 1); } : undefined} />;
}

function ExecutionResultLists({ lot, exit }: { lot?: ExecutionModel; exit?: ExecutionModel }) {
  const lotRows = lot?.evaluation.results ?? [];
  const exitRows = exit?.evaluation.results ?? [];
  const exitIds = new Set(exitRows.map(row => String(row.decision_id)));
  const incompleteExitPaths = lotRows.filter(row => !exitIds.has(String(row.decision_id))).length;
  const stamp = (value: string | number) => new Date(String(value)).toLocaleString("zh-CN", { hour12:false, month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" });
  const actionLabel = (value: string | number) => String(value) === "HOLD_TO_30M" ? "持有到30分钟" : String(value).replace(/^EXIT_(\d+)M$/, "$1分钟提前退出");
  return <section className="execution-results"><header><span>两套独立实验</span><h4>仓位倍率与退出动作分别评分。</h4><p>两套模型跟随同一个冻结方向，但不会拼成一笔组合成绩。各显示最近 10 笔；完整历史在上方曲线中。</p></header>
    <div className="execution-result-grid">
      <ExecutionResultPanel title="仓位倍率 OOS" count={lot?.evaluation.score_count ?? 0} visibleCount={Math.min(10, lotRows.length)} columns={["预测 / 方向", "选择", "模型收益", "固定1.0x", "差值"]}>
        {lotRows.slice().reverse().slice(0, 10).map(row => <article key={String(row.decision_id)}><span><b>{stamp(row.decision_time ?? row.time)} · {String(row.direction)}</b><small>结算 {stamp(row.scored_at ?? row.time)}</small></span><b>{String(row.selected_action)}</b><strong>{pct(Number(row.selected_quote_return))}</strong><span>{pct(Number(row.baseline_quote_return))}</span><strong className={Number(row.delta_quote_return) >= 0 ? "positive" : "negative"}>{pct(Number(row.delta_quote_return))}</strong></article>)}
        {!lotRows.length && <p>还没有成熟的仓位倍率 OOS。</p>}
      </ExecutionResultPanel>
      <ExecutionResultPanel title="提前退出 OOS" count={exit?.evaluation.score_count ?? 0} visibleCount={Math.min(10, exitRows.length)} columns={["预测 / 方向", "退出动作", "模型收益", "持有30m", "差值"]}>
        {exitRows.slice().reverse().slice(0, 10).map(row => <article key={String(row.decision_id)}><span><b>{stamp(row.decision_time ?? row.time)} · {String(row.direction)}</b><small>结算 {stamp(row.scored_at ?? row.time)}</small></span><b>{actionLabel(row.selected_action)}</b><strong>{pct(Number(row.selected_quote_return))}</strong><span>{pct(Number(row.baseline_quote_return))}</span><strong className={Number(row.delta_quote_return) >= 0 ? "positive" : "negative"}>{pct(Number(row.delta_quote_return))}</strong></article>)}
        {!exitRows.length && <p>还没有完整且成熟的退出路径。</p>}
      </ExecutionResultPanel>
    </div>
    {incompleteExitPaths > 0 && <p className="execution-path-note">另有 {formatExactCount(incompleteExitPaths)} 笔已成熟仓位缺少完整的 5/10/15/20/25 分钟因果检查路径，因此退出实验不评分；它们不会再显示成“等待退出”。</p>}
  </section>;
}

function ExecutionResultPanel({ title, count, visibleCount, columns, children }: { title: string; count: number; visibleCount: number; columns: string[]; children: ReactNode }) {
  return <section className="execution-result-panel"><header><b>{title}</b><span><strong>总计 <CountValue value={count} suffix=" 笔" /></strong><small>当前显示最新 {formatExactCount(visibleCount)} 笔</small></span></header><div className="execution-result-head">{columns.map(value => <span key={value}>{value}</span>)}</div>{children}</section>;
}

function ExecutionLineChart({ title, subtitle, points, sourceCount, downsampled, firstKey, secondKey, firstLabel, secondLabel, format, controls, loading, error }: {
  title: string; subtitle: string; points: Array<Record<string, string | number>>;
  sourceCount?: number; downsampled?: boolean;
  firstKey: string; secondKey: string; firstLabel: string; secondLabel: string;
  format: (value: number) => string; controls?: ReactNode; loading?: boolean; error?: () => void;
}) {
  if (!points.length) return <article className="execution-chart execution-chart-no-data">
    <div className="chart-caption"><div><b>{title}</b><span>{subtitle}</span></div></div>
    {controls}
    {loading ? <GraphLoading label="正在读取历史" compact /> : error ? <GraphLoadError label="历史读取失败" compact onRetry={error} /> : <div className="execution-chart-empty"><span>已经开始预测，但这个时间窗尚无成熟评分。</span></div>}
  </article>;
  const first = points.map(row => Number(row[firstKey] ?? 0));
  const second = points.map(row => Number(row[secondKey] ?? 0));
  const values = [0, ...first, ...second];
  const low = Math.min(...values); const high = Math.max(...values);
  const x = (index: number) => 62 + index / Math.max(1, points.length - 1) * 850;
  const y = (value: number) => 22 + (high - value) / Math.max(.000001, high - low) * 156;
  const line = (rows: number[]) => rows.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const stamp = (value: string | number) => new Date(String(value)).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  return <article className="execution-chart"><div className="chart-caption"><div><b>{title}</b><span>{subtitle}</span></div><strong>{format(first.at(-1) ?? 0)}<small>累计 {formatExactCount(sourceCount ?? points.length)} 笔{downsampled ? ` · 图中压缩为历史绘图点` : ""}</small></strong></div>
    {controls}
    {loading && <GraphLoading label="正在更新历史" compact />}
    {error && <GraphLoadError label="历史读取失败" compact onRetry={error} />}
    {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
    <div className="mobile-chart-scroll execution-chart-scroll" tabIndex={0} aria-label={`可左右滑动的${title}图表`}><svg viewBox="0 0 960 220" role="img" aria-label={title}>
      <line x1="64" x2="914" y1={y(0)} y2={y(0)} className="zero-line" />
      <text x="8" y={y(high)+4}>{format(high)}</text><text x="8" y={y(low)+4}>{format(low)}</text>
      <polyline points={line(first)} className="execution-primary-line" />
      <polyline points={line(second)} className="execution-baseline-line" />
      <text x="62" y="208">{stamp(points[0].time)}</text><text x="912" y="208" textAnchor="end">{stamp(points.at(-1)!.time)}</text>
    </svg></div>
    <div className="chart-legend"><span><i className="execution-primary-dot" />{firstLabel} <b>{format(first.at(-1) ?? 0)}</b></span><span><i className="execution-baseline-dot" />{secondLabel} <b>{format(second.at(-1) ?? 0)}</b></span></div>
  </article>;
}

function GraphLoading({ label = "正在读取数据", compact = false }: { label?: string; compact?: boolean }) {
  return <div className={`graph-loading${compact ? " graph-state-compact" : ""}`} role="status" aria-live="polite">
    <span className="graph-loading-bars" aria-hidden="true"><i /><i /><i /></span>
    <strong>{label}</strong>
  </div>;
}

function GraphLoadError({ onRetry, label = "数据读取失败", compact = false }: { onRetry: () => void; label?: string; compact?: boolean }) {
  return <div className={`graph-empty graph-load-error${compact ? " graph-state-compact" : ""}`} role="alert">
    <strong>{label}</strong>
    <button type="button" onClick={onRetry}>重新读取</button>
  </div>;
}

function Empty({ text, title, compact = false }: { text: string; title: string; compact?: boolean }) {
  return <div className={`graph-empty${compact ? " graph-state-compact" : ""}`}><strong>{title}</strong><p>{text}</p></div>;
}
