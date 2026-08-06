"use client";

import { useEffect, useMemo, useState } from "react";

type CurvePoint = { decision_time: string; model_version?: string; training_rows?: number; training_dataset_hash?: string; cumulative_quote_return: number };
type Curve = { model_identity: string; points: CurvePoint[] };
type Model = {
  model_identity: string; model_version: string; lifecycle_status: string;
  subsequent_oos_rows: number; cumulative_quote_return: number;
  profit_factor_quote_adjusted: number | null; distinct_days: number;
  coverage_rate: number | null; average_oracle_regret: number | null;
  wait_opportunity_cost: number;
};
type VersionGroup = {
  model_identity: string; training_dataset_hash: string; generation: number;
  lifecycle_status: string; created_at: string; latest_rebuild_at: string;
  training_rows: number; artifact_rebuilds: number; model_versions: string[];
  subsequent_oos_rows: number; cumulative_quote_return: number;
  profit_factor_quote_adjusted: number | null; distinct_days: number;
  coverage_rate: number | null; average_oracle_regret: number | null;
};
type Candle = { time: string; open: number; high: number; low: number; close: number; ticks: number };
type Decision = {
  source_decision_id: string; decision_time: string; exit_time: string;
  model_identity: string; recommended_action: string; prediction_status: string;
  outcome_status: string; value_quote_return: number | null;
  outcome_reason_codes?: string[];
  long_quote_return: number | null; short_quote_return: number | null;
  predicted_direction_u5: number | null; ev_long_u5: number | null;
  ev_short_u5: number | null; lcb_long_u5: number | null; lcb_short_u5: number | null;
};
type TrainingMarker = { model_identity: string; training_dataset_hash: string; created_at: string; training_rows: number; artifact_count: number };

const LABELS: Record<string, string> = {
  CHAMPION_0: "零收益基准", MARKET_ONLY: "黄金自身", NEWS_RESIDUAL: "官方新闻残差",
  FULL: "黄金＋官方新闻", BROAD_NEWS_RESIDUAL: "大视野新闻残差", BROAD_FULL: "黄金＋大视野新闻",
};
const COLORS: Record<string, string> = {
  MARKET_ONLY: "#8c5b16", NEWS_RESIDUAL: "#4169a1", FULL: "#476b19",
  BROAD_NEWS_RESIDUAL: "#7651a8", BROAD_FULL: "#c9362b", CHAMPION_0: "#777267",
};
const pct = (value: number) => `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(3)}%`;

export default function LearningGraphModal({
  open, onClose, curves, models, versionGroups, market,
}: {
  open: boolean; onClose: () => void; curves: Curve[]; models: Model[]; versionGroups: VersionGroup[];
  market?: { candles: Candle[]; decisions: Decision[]; training_markers: TrainingMarker[] };
}) {
  const [tab, setTab] = useState<"curve" | "versions" | "market">("curve");
  const [identity, setIdentity] = useState("BROAD_FULL");
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.body.classList.add("modal-open");
    window.addEventListener("keydown", close);
    return () => { document.body.classList.remove("modal-open"); window.removeEventListener("keydown", close); };
  }, [open, onClose]);
  if (!open) return null;
  return <div className="graph-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="graph-modal" role="dialog" aria-modal="true" aria-labelledby="graph-modal-title">
      <header><div><span>SHADOW EVIDENCE VISUALIZER</span><h2 id="graph-modal-title">模型与 XAUUSD 时间轴</h2></div><button type="button" onClick={onClose} aria-label="关闭图表">×</button></header>
      <nav aria-label="图表类型">
        <button className={tab === "curve" ? "active" : ""} onClick={() => setTab("curve")}>长期 OOS 曲线</button>
        <button className={tab === "versions" ? "active" : ""} onClick={() => setTab("versions")}>最新版 / 前一版</button>
        <button className={tab === "market" ? "active" : ""} onClick={() => setTab("market")}>K线与决策</button>
      </nav>
      <div className="graph-modal-body">
        {tab === "curve" && <LongCurve curves={curves} />}
        {tab === "versions" && <VersionComparison groups={versionGroups} />}
        {tab === "market" && <MarketChart market={market} identity={identity} setIdentity={setIdentity} />}
      </div>
      <footer><b>统一口径：</b> 所有曲线只使用模型创建后真正没见过的 30 分钟结果；WAIT 显示为灰色双向箭头，但收益固定为零，不会被画成一笔虚构交易。</footer>
    </section>
  </div>;
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
  return <div className="chart-block">
    <div className="chart-caption"><div><b>连续累计 OOS（换版本不归零）</b><span>橙色虚线是新版本首次参与评分的位置；同一时点会同时评分多个模型，但只算一个独立市场时点。</span></div><strong>{uniqueDecisionTimes} 个时点<small> · {all.length} 条模型评分</small></strong></div>
    <svg className="learning-svg" viewBox="0 0 960 380" role="img" aria-label="各模型累计 Live OOS 曲线">
      <line x1="58" x2="920" y1={y(0)} y2={y(0)} className="zero-line" />
      <text x="8" y={y(high) + 5}>{pct(high)}</text><text x="8" y={y(low) + 5}>{pct(low)}</text>
      {groupedBoundaries.map((boundary, index) => <g key={boundary.decision_time} className="version-boundary">
        <title>{boundary.changes.map(change => `${LABELS[change.model_identity] ?? change.model_identity} · 新训练数据代 · ${change.model_version}`).join("\n")}</title>
        <line x1={x(boundary.decision_time)} x2={x(boundary.decision_time)} y1="18" y2="350" />
        <text x={x(boundary.decision_time) + 4} y={24 + index % 2 * 14}>{boundary.changes[0]?.training_rows ?? ""}条新训练</text>
      </g>)}
      {usable.map(row => <polyline key={row.model_identity} fill="none" stroke={COLORS[row.model_identity]} strokeWidth="3" points={row.points.map(point => `${x(point.decision_time)},${y(point.cumulative_quote_return)}`).join(" ")} />)}
    </svg>
    <div className="chart-legend">{usable.map(row => <span key={row.model_identity}><i style={{ background: COLORS[row.model_identity] }} />{LABELS[row.model_identity]} <b>{pct(row.points.at(-1)?.cumulative_quote_return ?? 0)}</b></span>)}{groupedBoundaries.length > 0 && <span><i className="train-dot" />模型换版</span>}</div>
  </div>;
}

function VersionComparison({ groups }: { groups: VersionGroup[] }) {
  const identities = [...new Set(groups.map(row => row.model_identity))];
  return <div className="version-graph">
    <div className="chart-caption"><div><b>每个训练数据代的独立收益清单</b><span>每组从创建后归零独立评分；“恢复重建”沿用同一训练数据，不算新一组。</span></div></div>
    {identities.map(name => {
      const rows = groups.filter(row => row.model_identity === name).sort((a, b) => b.generation - a.generation);
      const diagnostic = name === "NEWS_RESIDUAL" || name === "BROAD_NEWS_RESIDUAL";
      return <article key={name}><h3>{LABELS[name] ?? name}<small>{diagnostic ? "诊断组件，不是完整交易策略" : "完整方向策略"}</small></h3><div>{rows.map(row => <section key={`${row.model_identity}-${row.training_dataset_hash}`} className={row.lifecycle_status === "LATEST" ? "is-latest" : ""}>
        <span>第 {row.generation} 组<br />{row.lifecycle_status === "LATEST" ? "最新版" : row.lifecycle_status === "PREVIOUS" ? "前一版" : "已归档"}</span>
        {row.subsequent_oos_rows ? <><strong>{pct(row.cumulative_quote_return)}</strong><small>{row.training_rows} 条训练 · {row.subsequent_oos_rows} 条 OOS · {row.distinct_days} 日 · PF {row.profit_factor_quote_adjusted?.toFixed(2) ?? "—"}</small><small>出方向 {(100 * (row.coverage_rate ?? 0)).toFixed(1)}% · 平均机会损失 {pct(row.average_oracle_regret ?? 0)}{row.artifact_rebuilds ? ` · 恢复重建 ${row.artifact_rebuilds} 次` : ""}</small></> : <><strong className="pending-score">等待结果</strong><small>{row.training_rows} 条训练 · 尚无创建后30分钟成熟样本</small></>}
      </section>)}</div></article>;
    })}
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
  const candidateDecisions = useMemo(() => scopedDecisions.filter(row =>
    ((row.recommended_action === "LONG" && showLong) ||
     (row.recommended_action === "SHORT" && showShort) ||
     (row.recommended_action === "WAIT" && showWait))
  ), [scopedDecisions, showLong, showShort, showWait]);
  const decisions = useMemo(() => {
    if (dense) return candidateDecisions;
    let previousIncluded = Number.POSITIVE_INFINITY;
    return [...candidateDecisions].reverse().filter(row => {
      const time = Date.parse(row.decision_time);
      if (time > previousIncluded - 1_800_000) return false;
      previousIncluded = time;
      return true;
    }).reverse();
  }, [candidateDecisions, dense]);
  if (!candles.length) return <Empty text="最近24小时还没有可绘制的本机 Bid/Ask 报价。" />;
  const low = Math.min(...candles.map(row => row.low)); const high = Math.max(...candles.map(row => row.high));
  const end = Date.parse(candles.at(-1)!.time) + 300_000;
  const xAtIndex = (index: number) => 55 + index / Math.max(1, candles.length - 1) * 870;
  const y = (value: number) => 24 + (high - value) / Math.max(.00001, high - low) * 320;
  const indexByTime = (time: string) => candles.reduce((best, row, index) =>
    Math.abs(Date.parse(row.time) - Date.parse(time)) < Math.abs(Date.parse(candles[best].time) - Date.parse(time)) ? index : best, 0);
  const xTime = (time: string) => Date.parse(time) > end ? 925 : xAtIndex(indexByTime(time));
  const byTime = (time: string) => candles[indexByTime(time)];
  const hiddenCount = candidateDecisions.length - decisions.length;
  const counts = scopedDecisions.reduce((total, row) => ({ ...total, [row.recommended_action]: total[row.recommended_action] + 1 }), { LONG: 0, SHORT: 0, WAIT: 0 } as Record<string, number>);
  const unhealthyWaits = scopedDecisions.filter(row => row.recommended_action === "WAIT" && row.prediction_status === "DATA_UNHEALTHY").length;
  const activeSelected = selected && decisions.some(row => row.source_decision_id === selected.source_decision_id && row.model_identity === selected.model_identity) ? selected : decisions.at(-1) ?? null;
  const selectedX = activeSelected ? xTime(activeSelected.decision_time) : null;
  const selectedExitX = activeSelected ? Math.min(925, xTime(activeSelected.exit_time)) : null;
  const timeLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const resultLabel = (value: number | null) => value == null ? "等待30分钟结果" : pct(value);
  return <div className="chart-block market-chart-block">
    <div className="chart-caption"><div><b>每根K线5分钟 · 每个箭头预测未来30分钟</b><span>绿色向上、红色向下、灰色双向代表 WAIT。新闻残差是诊断组件；完整方向请看“黄金＋新闻”。</span></div><select value={identity} onChange={event => { setIdentity(event.target.value); setSelected(null); }}>{Object.entries(LABELS).filter(([key]) => key !== "CHAMPION_0").map(([key, label]) => <option key={key} value={key}>{label}{key.includes("RESIDUAL") ? "（诊断）" : ""}</option>)}</select></div>
    <div className="market-controls" aria-label="K线图显示控制">
      <label>窗口<select value={hours} onChange={event => setHours(Number(event.target.value))}><option value="3">最近3小时</option><option value="6">最近6小时</option><option value="12">最近12小时</option><option value="24">最近24小时</option></select></label>
      <label>密度<select value={dense ? "all" : "clear"} onChange={event => setDense(event.target.value === "all")}><option value="clear">清晰：每30分钟1次</option><option value="all">全部：每5分钟预测</option></select></label>
      <button className={showLong ? "active" : ""} type="button" onClick={() => setShowLong(value => !value)}>看多 LONG</button>
      <button className={showShort ? "active" : ""} type="button" onClick={() => setShowShort(value => !value)}>看空 SHORT</button>
      <button className={showWait ? "active" : ""} type="button" onClick={() => setShowWait(value => !value)}>等待 WAIT</button>
      <button className={showTraining ? "active" : ""} type="button" onClick={() => setShowTraining(value => !value)}>模型换版位置</button>
      <span>显示 {decisions.length} 次{hiddenCount > 0 ? ` · 收起 ${hiddenCount} 次重复预测` : ""}</span>
    </div>
    <div className="prediction-counts"><b>本窗口全部原始预测</b><span>看多 {counts.LONG}</span><span>看空 {counts.SHORT}</span><span>等待 {counts.WAIT}{unhealthyWaits ? `（数据异常 ${unhealthyWaits}）` : ""}</span></div>
    <svg className="learning-svg" viewBox="0 0 960 380" role="img" aria-label="XAUUSD K线与模型决策">
      {candles.map((row, index) => { const cx = xAtIndex(index); const width = Math.max(1.5, 650 / candles.length); const up = row.close >= row.open; return <g key={row.time}><line x1={cx} x2={cx} y1={y(row.high)} y2={y(row.low)} stroke={up ? "#476b19" : "#c9362b"} /><rect x={cx - width / 2} width={width} y={Math.min(y(row.open), y(row.close))} height={Math.max(1, Math.abs(y(row.open) - y(row.close)))} fill={up ? "#476b19" : "#c9362b"} /></g>; })}
      {selectedX != null && selectedExitX != null && <g className="selected-window"><rect x={selectedX} width={Math.max(2, selectedExitX-selectedX)} y="18" height="332" /><line x1={selectedX} x2={selectedX} y1="18" y2="350" /><line x1={selectedExitX} x2={selectedExitX} y1="18" y2="350" /><text x={selectedX+4} y="32">预测</text><text x={Math.max(selectedX+36, selectedExitX-58)} y="32">30分钟后</text></g>}
      {decisions.map(row => { const candle = byTime(row.decision_time); const cx = xTime(row.decision_time); const action = row.recommended_action; const cy = action === "LONG" ? y(candle.low) + 12 : action === "SHORT" ? y(candle.high) - 12 : y(candle.close); const color = action === "LONG" ? "#476b19" : action === "SHORT" ? "#c9362b" : "#777267"; const isSelected = activeSelected?.source_decision_id === row.source_decision_id && activeSelected?.model_identity === row.model_identity; return <g key={`${row.source_decision_id}-${row.model_identity}`} role="button" tabIndex={0} className={`decision-marker${isSelected ? " selected" : ""}`} onClick={() => setSelected(row)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") setSelected(row); }}><title>{`${timeLabel(row.decision_time)} · ${action} · 点击查看30分钟结果`}</title>{isSelected && <circle cx={cx} cy={cy} r="11" fill="none" stroke={color} strokeWidth="2" />}{action === "WAIT" ? <path d={`M ${cx-8} ${cy} l 5 -5 v3 h6 v-3 l5 5 -5 5 v-3 h-6 v3 z`} fill={color} /> : <path d={action === "LONG" ? `M ${cx} ${cy-7} l -6 11 h 12 z` : `M ${cx} ${cy+7} l -6 -11 h 12 z`} fill={color} />}</g>; })}
      {showTraining && (market?.training_markers ?? []).filter(row => row.model_identity === identity && Date.parse(row.created_at) >= cutoff).map(row => <g key={`${row.model_identity}-${row.training_dataset_hash}`}><title>{`${timeLabel(row.created_at)} · 第一次使用 ${row.training_rows} 条训练数据${row.artifact_count > 1 ? ` · 后续恢复重建 ${row.artifact_count-1} 次` : ""}`}</title><line x1={xTime(row.created_at)} x2={xTime(row.created_at)} y1="18" y2="350" className="training-line" /><text x={xTime(row.created_at)+4} y="347" className="training-label">{row.training_rows}条新训练</text></g>)}
      <text x="5" y="30">{high.toFixed(2)}</text><text x="5" y="350">{low.toFixed(2)}</text>
    </svg>
    <div className="chart-legend"><span><i className="long-dot" />看多预测</span><span><i className="short-dot" />看空预测</span>{showWait && <span><i className="wait-dot" />↔ 等待，不持仓</span>}{showTraining && <span><i className="train-dot" />新训练数据代</span>}</div>
    <div className="decision-reader" aria-live="polite">{activeSelected ? <>
      <div><small>一次完整观察</small><strong>{timeLabel(activeSelected.decision_time)} 预测 {activeSelected.recommended_action}</strong><span>→ {timeLabel(activeSelected.exit_time)} 固定观察结果</span></div>
      <DecisionPayoff selected={activeSelected} resultLabel={resultLabel} />
    </> : <><div><small>怎样阅读</small><strong>点击图中的三角形</strong><span>这里只显示一次预测；选中后才标出它对应的30分钟观察窗口。</span></div></>}</div>
    <p className="wait-explainer"><b>WAIT 怎样产生：</b> Ridge 学习的是未来30分钟连续收益，不是硬猜三分类。Long、Wait=0、Short 三种结果都会被评分；只有最佳方向扣除 Bid/Ask 成本与95%不确定性后仍为正，才显示方向，否则 WAIT。系统同时记录 WAIT 错过的事后机会，但不会用惩罚强迫开仓。</p>
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

function Empty({ text }: { text: string }) { return <div className="graph-empty"><strong>等待可验证数据</strong><p>{text}</p></div>; }
