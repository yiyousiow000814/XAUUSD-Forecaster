"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

type CurvePoint = { decision_time: string; model_version?: string; training_rows?: number; training_dataset_hash?: string; cumulative_quote_return: number };
type Curve = { model_identity: string; points: CurvePoint[] };
type Candle = { time: string; open: number; high: number; low: number; close: number; ticks: number };
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
type VersionGroup = {
  model_identity: string; training_dataset_hash: string; generation: number;
  lifecycle_status: "LATEST" | "PREVIOUS" | "ARCHIVED"; created_at: string;
  latest_rebuild_at: string; training_rows: number; artifact_rebuilds: number;
  model_versions: string[]; subsequent_oos_rows: number; distinct_days: number;
  cumulative_quote_return: number; profit_factor_quote_adjusted: number | null;
  coverage_rate: number | null; average_oracle_regret: number | null;
};
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
type GraphTab = "curve" | "versions" | "market" | "execution";

const LABELS: Record<string, string> = {
  CHAMPION_0: "零收益基准", MARKET_ONLY: "黄金自身", NEWS_RESIDUAL: "官方新闻修正量",
  FULL: "黄金＋官方新闻", BROAD_NEWS_RESIDUAL: "大视野新闻修正量", BROAD_FULL: "黄金＋大视野新闻",
};
const COLORS: Record<string, string> = {
  MARKET_ONLY: "#8c5b16", NEWS_RESIDUAL: "#4169a1", FULL: "#476b19",
  BROAD_NEWS_RESIDUAL: "#7651a8", BROAD_FULL: "#c9362b", CHAMPION_0: "#777267",
};
const pct = (value: number) => `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(3)}%`;

export default function LearningGraphModal({
  open, onClose, startTab, curves, market, versionGroups, execution,
}: {
  open: boolean; onClose: () => void; startTab?: "curve" | "execution"; curves: Curve[];
  market?: { candles: Candle[]; decisions: Decision[]; training_markers: TrainingMarker[]; decision_resource?: string };
  versionGroups: VersionGroup[]; execution?: ExecutionLearning;
}) {
  const [tab, setTab] = useState<GraphTab>(startTab ?? "curve");
  const [identity, setIdentity] = useState("BROAD_FULL");
  const [remoteMarket, setRemoteMarket] = useState<typeof market>();
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.body.classList.add("modal-open");
    window.addEventListener("keydown", close);
    return () => { document.body.classList.remove("modal-open"); window.removeEventListener("keydown", close); };
  }, [open, onClose]);
  useEffect(() => {
    if (!open || !market?.decision_resource) return;
    let cancelled = false;
    fetch(market.decision_resource, { cache: "no-store" })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(body => { if (!cancelled) setRemoteMarket(body); })
      .catch(() => { /* The status snapshot remains a safe empty fallback. */ });
    return () => { cancelled = true; };
  }, [open, market]);
  const resolvedMarket = market?.decision_resource ? remoteMarket ?? market : market;
  if (!open) return null;
  return <div className="graph-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className={`graph-modal graph-modal-${tab}`} role="dialog" aria-modal="true" aria-labelledby="graph-modal-title">
      <header><div><span>SHADOW EVIDENCE VISUALIZER</span><h2 id="graph-modal-title">模型与 XAUUSD 时间轴</h2></div><button type="button" onClick={onClose} aria-label="关闭图表">×</button></header>
      <nav aria-label="图表类型">
        <button className={tab === "curve" ? "active" : ""} onClick={() => setTab("curve")}>长期 OOS 曲线</button>
        <button className={tab === "versions" ? "active" : ""} onClick={() => setTab("versions")}>每组独立成绩</button>
        <button className={tab === "market" ? "active" : ""} onClick={() => setTab("market")}>K线与决策</button>
        <button className={tab === "execution" ? "active" : ""} onClick={() => setTab("execution")}>仓位与退出</button>
      </nav>
      <div className="graph-modal-body">
        {tab === "curve" && <LongCurve curves={curves} />}
        {tab === "versions" && <VersionLedger groups={versionGroups} />}
        {tab === "market" && <MarketChart market={resolvedMarket} identity={identity} setIdentity={setIdentity} />}
        {tab === "execution" && <ExecutionCharts execution={execution} />}
      </div>
      <footer><b>统一口径：</b> 所有曲线只使用模型创建后真正没见过的 30 分钟结果；WAIT 显示为灰色双向箭头，但收益固定为零，不会被画成一笔虚构交易。</footer>
    </section>
  </div>;
}

function VersionLedger({ groups }: { groups: VersionGroup[] }) {
  const [identity, setIdentity] = useState("BROAD_FULL");
  const rows = groups.filter(row => row.model_identity === identity).sort((a,b) => b.generation-a.generation);
  const stamp = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12:false, month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" });
  return <section className="version-ledger modal-version-ledger"><header><div><span>每个训练数据代 · 独立从零评分</span><h3>版本独立盈亏清单</h3></div><select value={identity} onChange={event => setIdentity(event.target.value)}>{Object.entries(LABELS).filter(([key]) => key !== "CHAMPION_0").map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></header>
    <div className="version-ledger-head"><span>组别 / 状态</span><span>训练与上线</span><span>创建后 OOS</span><span>本组独立收益</span><span>PF / 出方向</span></div>
    {rows.map(row => <article key={`${row.model_identity}-${row.training_dataset_hash}`} className={row.lifecycle_status === "LATEST" ? "is-latest" : ""}>
      <span><b>第 {row.generation} 组</b><small>{row.lifecycle_status === "LATEST" ? "最新版" : row.lifecycle_status === "PREVIOUS" ? "前一版" : "已归档"}</small></span>
      <span><b>{row.training_rows} 条</b><small>{stamp(row.created_at)}{row.artifact_rebuilds ? ` · 恢复重建 ${row.artifact_rebuilds} 次` : ""}</small></span>
      <span><b>{row.subsequent_oos_rows} 条</b><small>{row.distinct_days} 个日期</small></span>
      <strong>{row.subsequent_oos_rows ? pct(row.cumulative_quote_return) : "等待结果"}</strong>
      <span><b>{row.profit_factor_quote_adjusted?.toFixed(2) ?? "—"}</b><small>出方向 {((row.coverage_rate ?? 0)*100).toFixed(1)}%</small></span>
    </article>)}
    {!rows.length && <p>这个模型还没有真实训练版本。</p>}
  </section>;
}

function LongCurve({ curves }: { curves: Curve[] }) {
  const usable = curves.filter(row => row.model_identity !== "CHAMPION_0" && row.points.length > 0);
  const all = usable.flatMap(row => row.points);
  if (!all.length) return <Empty text="还没有已成熟的 Live OOS 点；第一个预测走完30分钟后才会出现。" />;
  const uniqueDecisionTimes = new Set(all.map(point => point.decision_time)).size;
  const start = Math.min(...all.map(point => Date.parse(point.decision_time)));
  const end = Math.max(...all.map(point => Date.parse(point.decision_time)));
  const values = all.map(point => point.cumulative_quote_return).concat(0);
  const low = Math.min(...values); const high = Math.max(...values);
  const x = (time: string) => 58 + (Date.parse(time) - start) / Math.max(1, end - start) * 862;
  const y = (value: number) => 28 + (high - value) / Math.max(.000001, high - low) * 310;
  const tickTimes = Array.from(new Set([0, .25, .5, .75, 1].map(part => new Date(start + (end - start) * part).toISOString())));
  const axisLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const versionBoundaries = usable.flatMap(row => row.points.flatMap((point, index) => {
    if (index === 0 || !point.model_version) return [];
    return [{
      decision_time: point.decision_time,
      model_identity: row.model_identity,
      model_version: point.model_version,
      training_rows: point.training_rows,
    }];
  }));
  const groupedBoundaries = [...new Set(versionBoundaries.map(row => row.decision_time))].map(decisionTime => ({
    decision_time: decisionTime,
    changes: versionBoundaries.filter(row => row.decision_time === decisionTime),
  }));
  return <div className="chart-block long-curve-block">
    <div className="chart-caption"><div><b>历史＋实时成熟 OOS（只追加，不重写）</b><span>每个30分钟结果成熟后追加到右端；模型换版本不会清零，已存在的历史点不会重新计算。</span></div><strong>{uniqueDecisionTimes} 个时点<small> · {all.length} 条模型评分</small></strong></div>
    <svg className="learning-svg" viewBox="0 0 960 400" role="img" aria-label="各模型历史与实时成熟 OOS 曲线">
      <line x1="58" x2="920" y1={y(0)} y2={y(0)} className="zero-line" />
      <text x="8" y={y(high) + 5}>{pct(high)}</text><text x="8" y={y(low) + 5}>{pct(low)}</text>
      {groupedBoundaries.map((boundary, index) => <g key={boundary.decision_time} className="version-boundary">
        <title>{boundary.changes.map(change => `${LABELS[change.model_identity] ?? change.model_identity} · 新训练数据代 · ${change.model_version}`).join("\n")}</title>
        <line x1={x(boundary.decision_time)} x2={x(boundary.decision_time)} y1="18" y2="350" />
        <text x={x(boundary.decision_time) + 4} y={24 + index % 2 * 14}>{boundary.changes[0]?.training_rows ?? ""}条新训练</text>
      </g>)}
      {usable.map(row => <polyline key={row.model_identity} fill="none" stroke={COLORS[row.model_identity]} strokeWidth="3" points={row.points.map(point => `${x(point.decision_time)},${y(point.cumulative_quote_return)}`).join(" ")} />)}
      {tickTimes.map(value => <g key={value} className="time-axis"><line x1={x(value)} x2={x(value)} y1="350" y2="356" /><text x={x(value)} y="374" textAnchor="middle">{axisLabel(value)}</text></g>)}
    </svg>
    <div className="chart-legend">{usable.map(row => <span key={row.model_identity}><i style={{ background: COLORS[row.model_identity] }} />{LABELS[row.model_identity]} <b>{pct(row.points.at(-1)?.cumulative_quote_return ?? 0)}</b></span>)}{groupedBoundaries.length > 0 && <span><i className="train-dot" />模型换版本</span>}</div>
  </div>;
}

function MarketChart({ market, identity, setIdentity }: { market?: { candles: Candle[]; decisions: Decision[]; training_markers: TrainingMarker[] }; identity: string; setIdentity: (value: string) => void }) {
  const [hours, setHours] = useState(3);
  const [showLong, setShowLong] = useState(true);
  const [showShort, setShowShort] = useState(true);
  const [showWait, setShowWait] = useState(true);
  const [dense, setDense] = useState(false);
  const [showTraining, setShowTraining] = useState(false);
  const [selected, setSelected] = useState<Decision | null>(null);
  const allCandles = market?.candles ?? [];
  const endTime = allCandles.length ? Date.parse(allCandles.at(-1)!.time) + 300_000 : 0;
  const cutoff = endTime - hours * 3_600_000;
  const candles = allCandles.filter(row => Date.parse(row.time) >= cutoff);
  const scopedDecisions = useMemo(() => (market?.decisions ?? []).filter(row =>
    row.model_identity === identity && Date.parse(row.decision_time) >= cutoff
  ), [market, identity, cutoff]);
  const arrowAction = (row: Decision) => (
    row.ev_long_u5 != null && row.ev_short_u5 != null && row.ev_long_u5 !== row.ev_short_u5
      ? row.ev_long_u5 > row.ev_short_u5 ? "LONG" : "SHORT"
      : "WAIT"
  );
  const candidateDecisions = scopedDecisions.filter(row =>
    ((arrowAction(row) === "LONG" && showLong) ||
     (arrowAction(row) === "SHORT" && showShort) ||
     (arrowAction(row) === "WAIT" && showWait))
  );
  const decisions = (() => {
    if (dense) return candidateDecisions;
    return candidateDecisions.filter(row => new Date(row.decision_time).getUTCMinutes() % 30 === 0);
  })();
  if (!candles.length) return <Empty text="最近24小时还没有可绘制的本机 Bid/Ask 报价。" />;
  const low = Math.min(...candles.map(row => row.low)); const high = Math.max(...candles.map(row => row.high));
  const end = Date.parse(candles.at(-1)!.time) + 300_000;
  const xAtIndex = (index: number) => 55 + index / Math.max(1, candles.length - 1) * 870;
  const y = (value: number) => 58 + (high - value) / Math.max(.00001, high - low) * 274;
  const indexByTime = (time: string) => candles.reduce((best, row, index) =>
    Math.abs(Date.parse(row.time) - Date.parse(time)) < Math.abs(Date.parse(candles[best].time) - Date.parse(time)) ? index : best, 0);
  const xTime = (time: string) => Date.parse(time) > end ? 925 : xAtIndex(indexByTime(time));
  const byTime = (time: string) => candles[indexByTime(time)];
  const hiddenByAction = scopedDecisions.length - candidateDecisions.length;
  const hiddenByFrequency = candidateDecisions.length - decisions.length;
  const counts = decisions.reduce((total, row) => ({ ...total, [arrowAction(row)]: total[arrowAction(row)] + 1 }), { LONG: 0, SHORT: 0, WAIT: 0 } as Record<string, number>);
  const unhealthyWaits = decisions.filter(row => row.recommended_action === "WAIT" && row.prediction_status === "DATA_UNHEALTHY").length;
  const policyMismatchCount = decisions.filter(row => row.policy_consistent === false).length;
  const activeSelected = selected && decisions.some(row => row.source_decision_id === selected.source_decision_id && row.model_identity === selected.model_identity && row.model_version === selected.model_version) ? selected : decisions.at(-1) ?? null;
  const selectedX = activeSelected ? xTime(activeSelected.decision_time) : null;
  const exitTime = (row: Decision) => row.exit_time ?? new Date(Date.parse(row.decision_time) + 30 * 60_000).toISOString();
  const selectedExitX = activeSelected ? Math.min(925, xTime(exitTime(activeSelected))) : null;
  const timeLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const axisTimeLabel = (value: string) => new Date(value).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
  const timeTickIndices = Array.from(new Set([0, .25, .5, .75, 1].map(part => Math.round((candles.length - 1) * part))));
  const resultLabel = (value: number | null) => value == null ? "等待30分钟结果" : pct(value);
  return <div className="chart-block market-chart-block">
    <div className="chart-caption"><div><b>每根K线5分钟 · 每个箭头预测未来30分钟</b><span>绿色向上、红色向下、灰色双向代表 WAIT。新闻残差只表示新闻对黄金基线的修正量；完整方向请看“黄金＋新闻”。</span></div><select value={identity} onChange={event => { setIdentity(event.target.value); setSelected(null); }}>{Object.entries(LABELS).filter(([key]) => key !== "CHAMPION_0").map(([key, label]) => <option key={key} value={key}>{label}{key.includes("RESIDUAL") ? "（修正量）" : ""}</option>)}</select></div>
    <div className="market-controls" aria-label="K线图显示控制">
      <label>窗口<select value={hours} onChange={event => setHours(Number(event.target.value))}><option value="3">最近3小时</option><option value="6">最近6小时</option><option value="12">最近12小时</option><option value="24">最近24小时</option></select></label>
      <label>频率<select value={dense ? "all" : "clear"} onChange={event => setDense(event.target.value === "all")}><option value="clear">每小时 :00 / :30</option><option value="all">每5分钟</option></select></label>
      <button className={showLong ? "active" : ""} type="button" onClick={() => setShowLong(value => !value)}>看多 LONG</button>
      <button className={showShort ? "active" : ""} type="button" onClick={() => setShowShort(value => !value)}>看空 SHORT</button>
      <button className={showWait ? "active" : ""} type="button" onClick={() => setShowWait(value => !value)}>等待 WAIT</button>
      <button className={showTraining ? "active" : ""} type="button" onClick={() => setShowTraining(value => !value)}>模型换版本</button>
      <span>显示 {decisions.length} 次{hiddenByAction > 0 ? ` · 动作筛选隐藏 ${hiddenByAction} 次` : ""}{hiddenByFrequency > 0 ? ` · 频率收起 ${hiddenByFrequency} 次` : ""}</span>
    </div>
    <div className="prediction-counts"><b>成本后EV较高方向</b><span>看多 {counts.LONG}</span><span>看空 {counts.SHORT}</span><span>等待 {counts.WAIT}{unhealthyWaits ? `（数据异常 ${unhealthyWaits}）` : ""}</span>{policyMismatchCount > 0 && <span className="negative">历史规则不一致 {policyMismatchCount}（原记录保留）</span>}</div>
    <svg className="learning-svg" viewBox="0 0 960 400" role="img" aria-label="XAUUSD K线与模型决策">
      {candles.map((row, index) => { const cx = xAtIndex(index); const width = Math.max(1.5, 650 / candles.length); const up = row.close >= row.open; return <g key={row.time}><line x1={cx} x2={cx} y1={y(row.high)} y2={y(row.low)} stroke={up ? "#476b19" : "#c9362b"} /><rect x={cx - width / 2} width={width} y={Math.min(y(row.open), y(row.close))} height={Math.max(1, Math.abs(y(row.open) - y(row.close)))} fill={up ? "#476b19" : "#c9362b"} /></g>; })}
      {selectedX != null && selectedExitX != null && <g className="selected-window"><rect x={selectedX} width={Math.max(2, selectedExitX-selectedX)} y="52" height="280" /><line x1={selectedX} x2={selectedX} y1="52" y2="332" /><line x1={selectedExitX} x2={selectedExitX} y1="52" y2="332" /><text x={selectedX+4} y="49">预测</text><text x={Math.max(selectedX+36, selectedExitX-58)} y="49">30分钟后</text></g>}
      {decisions.map(row => { const candle = byTime(row.decision_time); const cx = xTime(row.decision_time); const action = arrowAction(row); const cy = action === "WAIT" ? 34 : action === "LONG" ? y(candle.low) + 12 : y(candle.high) - 12; const color = action === "LONG" ? "#476b19" : action === "SHORT" ? "#c9362b" : "#555149"; const isSelected = activeSelected?.source_decision_id === row.source_decision_id && activeSelected?.model_identity === row.model_identity && activeSelected?.model_version === row.model_version; return <g key={`${row.source_decision_id}-${row.model_identity}-${row.model_version}`} role="button" tabIndex={0} className={`decision-marker${isSelected ? " selected" : ""}${row.policy_consistent === false ? " policy-mismatch" : ""}`} onClick={() => setSelected(row)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") setSelected(row); }}><title>{`${timeLabel(row.decision_time)} · 成本后EV较高方向 ${action} · 模型版本 ${row.model_version} · 点击查看30分钟结果`}</title>{action === "WAIT" && <circle cx={cx} cy={cy} r="10" fill="#eee9da" stroke={color} strokeWidth="1.5" />}{isSelected && <circle cx={cx} cy={cy} r="14" fill="none" stroke={color} strokeWidth="2" />}{action === "WAIT" ? <path d={`M ${cx-7} ${cy} h 14 M ${cx-7} ${cy} l 4 -4 M ${cx-7} ${cy} l 4 4 M ${cx+7} ${cy} l -4 -4 M ${cx+7} ${cy} l -4 4`} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" /> : <path d={action === "LONG" ? `M ${cx} ${cy-7} l -6 11 h 12 z` : `M ${cx} ${cy+7} l -6 -11 h 12 z`} fill={color} />}</g>; })}
      {showTraining && (market?.training_markers ?? []).filter(row => row.model_identity === identity && Date.parse(row.created_at) >= cutoff).map(row => <g key={`${row.model_identity}-${row.training_dataset_hash}`}><title>{`${timeLabel(row.created_at)} · 第一次使用 ${row.training_rows} 条训练数据${row.artifact_count > 1 ? ` · 后续恢复重建 ${row.artifact_count-1} 次` : ""}`}</title><line x1={xTime(row.created_at)} x2={xTime(row.created_at)} y1="52" y2="332" className="training-line" /><text x={xTime(row.created_at)+4} y="328" className="training-label">{row.training_rows}条新训练</text></g>)}
      <text x="5" y="64">{high.toFixed(2)}</text><text x="5" y="335">{low.toFixed(2)}</text>
      {timeTickIndices.map(index => <g key={candles[index].time} className="time-axis"><line x1={xAtIndex(index)} x2={xAtIndex(index)} y1="338" y2="344" /><text x={xAtIndex(index)} y="366" textAnchor="middle">{axisTimeLabel(candles[index].time)}</text></g>)}
    </svg>
    <div className="chart-legend"><span><i className="long-dot" />看多预测</span><span><i className="short-dot" />看空预测</span>{showWait && <span><i className="wait-dot" />↔ 等待，不持仓</span>}{showTraining && <span><i className="train-dot" />新训练数据代</span>}</div>
    <div className="decision-reader" aria-live="polite">{activeSelected ? <>
      <div><small>一次完整观察</small><strong>{timeLabel(activeSelected.decision_time)} · 成本后EV较高 {arrowAction(activeSelected)}</strong><span>版本 {activeSelected.model_version} · → {timeLabel(exitTime(activeSelected))} 固定观察结果{activeSelected.policy_consistent === false ? ` · 当时规则校验异常（原记录保留；应为 ${activeSelected.policy_expected_action}）` : ""}</span></div>
      <DecisionPayoff selected={activeSelected} resultLabel={resultLabel} />
    </> : <><div><small>怎样阅读</small><strong>点击图中的三角形</strong><span>这里只显示一次预测；选中后才标出它对应的30分钟观察窗口。</span></div></>}</div>
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

function ExecutionCharts({ execution }: { execution?: ExecutionLearning }) {
  const lot = execution?.models.find(row => row.model_identity === "LOT_RIDGE");
  const exit = execution?.models.find(row => row.model_identity === "EXIT_RIDGE");
  if (!lot && !exit) return <Empty text="仓位与退出模型还没有生成可评分的前向预测。" />;
  return <section className="execution-charts">
    <header><span>CAUSAL EXECUTION OOS</span><h3>跟随同一个 Live 方向，逐笔看仓位与退出。</h3><p>方向固定来自 {execution?.source_model_label ?? "黄金＋大视野新闻 Ridge"}。WAIT 不创建仓位；历史结果只训练，下面只评分模型上线后真正发生的未来位置。</p></header>
    <div className="execution-scorecards">
      <article><small>仓位倍率 Ridge</small><strong>{lot?.evaluation.score_count ?? 0} 笔已评分</strong><span>每个方向位置只比较 0.5x / 1.0x / 2.0x</span></article>
      <article><small>Exit Ridge</small><strong>{exit?.evaluation.score_count ?? 0} 笔已评分</strong><span>{exit?.predictions ?? 0} 次途中检查 · 提前退出 {exit?.action_counts?.EXIT ?? 0} 次 · 继续持有 {exit?.action_counts?.HOLD ?? 0} 次</span></article>
    </div>
    {(exit?.predictions ?? 0) > 0 && (exit?.action_counts?.EXIT ?? 0) === 0 && <p className="execution-callout"><b>目前没有提前退出。</b> Exit Ridge 已正常检查，但每次预测的“从当前继续持有到30分钟”的收益都大于零，因此全部选择继续持有。这是当前模型结果，不是页面遗漏。</p>}
      <ExecutionLineChart title="仓位倍率：模型选择 vs 固定 1.0x" subtitle="每个点是一笔冻结 Live 方向产生的未来位置；0.5x / 1.0x / 2.0x 只改变同一方向的倍率。" points={lot?.evaluation.points ?? []} sourceCount={lot?.evaluation.chart_source_count} downsampled={lot?.evaluation.chart_downsampled} firstKey="selected_cumulative_return" secondKey="baseline_cumulative_return" firstLabel="Ridge 倍率" secondLabel="固定 1.0x" format={pct} />
    <ExecutionLineChart title="退出：顺序 Exit Ridge vs 固定持有30分钟" subtitle="每个位置从5分钟开始依次检查；一旦 EXIT，后续检查停止。曲线是整段位置收益，不再累加重复检查点。" points={exit?.evaluation.points ?? []} sourceCount={exit?.evaluation.chart_source_count} downsampled={exit?.evaluation.chart_downsampled} firstKey="selected_cumulative_return" secondKey="baseline_cumulative_return" firstLabel="顺序 Exit Ridge" secondLabel="固定30分钟" format={pct} />
    <ExecutionResultLists lot={lot} exit={exit} />
  </section>;
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
    {incompleteExitPaths > 0 && <p className="execution-path-note">另有 {incompleteExitPaths} 笔已成熟仓位缺少完整的 5/10/15/20/25 分钟因果检查路径，因此退出实验不评分；它们不会再显示成“等待退出”。</p>}
  </section>;
}

function ExecutionResultPanel({ title, count, visibleCount, columns, children }: { title: string; count: number; visibleCount: number; columns: string[]; children: ReactNode }) {
  return <section className="execution-result-panel"><header><b>{title}</b><span><strong>总计 {count} 笔</strong><small>当前显示最新 {visibleCount} 笔</small></span></header><div className="execution-result-head">{columns.map(value => <span key={value}>{value}</span>)}</div>{children}</section>;
}

function ExecutionLineChart({ title, subtitle, points, sourceCount, downsampled, firstKey, secondKey, firstLabel, secondLabel, format }: {
  title: string; subtitle: string; points: Array<Record<string, string | number>>;
  sourceCount?: number; downsampled?: boolean;
  firstKey: string; secondKey: string; firstLabel: string; secondLabel: string;
  format: (value: number) => string;
}) {
  if (!points.length) return <div className="execution-chart-empty"><b>{title}</b><span>已经开始预测，但尚无成熟评分。</span></div>;
  const first = points.map(row => Number(row[firstKey] ?? 0));
  const second = points.map(row => Number(row[secondKey] ?? 0));
  const values = [0, ...first, ...second];
  const low = Math.min(...values); const high = Math.max(...values);
  const x = (index: number) => 64 + index / Math.max(1, points.length - 1) * 850;
  const y = (value: number) => 34 + (high - value) / Math.max(.000001, high - low) * 230;
  const line = (rows: number[]) => rows.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const stamp = (value: string | number) => new Date(String(value)).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  return <article className="execution-chart"><div className="chart-caption"><div><b>{title}</b><span>{subtitle}</span></div><strong>{format(first.at(-1) ?? 0)}<small> · 累计 {sourceCount ?? points.length} 笔{downsampled ? ` · 图中压缩为 ${points.length} 点` : ""}</small></strong></div>
    <svg viewBox="0 0 960 320" role="img" aria-label={title}>
      <line x1="64" x2="914" y1={y(0)} y2={y(0)} className="zero-line" />
      <text x="8" y={y(high)+4}>{format(high)}</text><text x="8" y={y(low)+4}>{format(low)}</text>
      <polyline points={line(first)} className="execution-primary-line" />
      <polyline points={line(second)} className="execution-baseline-line" />
      <text x="64" y="300">{stamp(points[0].time)}</text><text x="914" y="300" textAnchor="end">{stamp(points.at(-1)!.time)}</text>
    </svg>
    <div className="chart-legend"><span><i className="execution-primary-dot" />{firstLabel} <b>{format(first.at(-1) ?? 0)}</b></span><span><i className="execution-baseline-dot" />{secondLabel} <b>{format(second.at(-1) ?? 0)}</b></span></div>
  </article>;
}

function Empty({ text }: { text: string }) { return <div className="graph-empty"><strong>等待可验证数据</strong><p>{text}</p></div>; }
