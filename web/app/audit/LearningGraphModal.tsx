"use client";

import { useEffect, useMemo, useState } from "react";

type CurvePoint = { decision_time: string; cumulative_quote_return: number };
type Curve = { model_identity: string; points: CurvePoint[] };
type Model = {
  model_identity: string; model_version: string; lifecycle_status: string;
  subsequent_oos_rows: number; cumulative_quote_return: number;
  profit_factor_quote_adjusted: number | null; distinct_days: number;
  coverage_rate: number | null; average_oracle_regret: number | null;
  wait_opportunity_cost: number;
};
type Candle = { time: string; open: number; high: number; low: number; close: number; ticks: number };
type Decision = {
  source_decision_id: string; decision_time: string; exit_time: string;
  model_identity: string; recommended_action: string; prediction_status: string;
  outcome_status: string; value_quote_return: number | null;
  long_quote_return: number | null; short_quote_return: number | null;
  predicted_direction_u5: number | null; ev_long_u5: number | null;
  ev_short_u5: number | null; lcb_long_u5: number | null; lcb_short_u5: number | null;
};
type TrainingMarker = { model_identity: string; model_version: string; created_at: string; training_rows: number };

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
  open, onClose, curves, models, market,
}: {
  open: boolean; onClose: () => void; curves: Curve[]; models: Model[];
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
        {tab === "versions" && <VersionComparison models={models} />}
        {tab === "market" && <MarketChart market={market} identity={identity} setIdentity={setIdentity} />}
      </div>
      <footer><b>统一口径：</b> 所有曲线只使用模型创建后真正没见过的 30 分钟结果；WAIT 仍记录反事实，但不会被画成一笔虚构交易。</footer>
    </section>
  </div>;
}

function LongCurve({ curves }: { curves: Curve[] }) {
  const usable = curves.filter(row => row.model_identity !== "CHAMPION_0" && row.points.length > 0);
  const all = usable.flatMap(row => row.points);
  if (!all.length) return <Empty text="还没有已成熟的 Live OOS 点；第一个预测走完30分钟后才会出现。" />;
  const start = Math.min(...all.map(point => Date.parse(point.decision_time)));
  const end = Math.max(...all.map(point => Date.parse(point.decision_time)));
  const values = all.map(point => point.cumulative_quote_return).concat(0);
  const low = Math.min(...values); const high = Math.max(...values);
  const x = (time: string) => 58 + (Date.parse(time) - start) / Math.max(1, end - start) * 862;
  const y = (value: number) => 28 + (high - value) / Math.max(.000001, high - low) * 310;
  return <div className="chart-block">
    <div className="chart-caption"><div><b>连续累计 OOS（换版本不归零）</b><span>每个身份只取当时最新冻结版本，版本切换后继续沿同一时间轴累加。</span></div><strong>{all.length} 个成熟点</strong></div>
    <svg className="learning-svg" viewBox="0 0 960 380" role="img" aria-label="各模型累计 Live OOS 曲线">
      <line x1="58" x2="920" y1={y(0)} y2={y(0)} className="zero-line" />
      <text x="8" y={y(high) + 5}>{pct(high)}</text><text x="8" y={y(low) + 5}>{pct(low)}</text>
      {usable.map(row => <polyline key={row.model_identity} fill="none" stroke={COLORS[row.model_identity]} strokeWidth="3" points={row.points.map(point => `${x(point.decision_time)},${y(point.cumulative_quote_return)}`).join(" ")} />)}
    </svg>
    <div className="chart-legend">{usable.map(row => <span key={row.model_identity}><i style={{ background: COLORS[row.model_identity] }} />{LABELS[row.model_identity]} <b>{pct(row.points.at(-1)?.cumulative_quote_return ?? 0)}</b></span>)}</div>
  </div>;
}

function VersionComparison({ models }: { models: Model[] }) {
  const identities = [...new Set(models.map(row => row.model_identity))];
  return <div className="version-graph">
    <div className="chart-caption"><div><b>同一身份的两代冻结模型</b><span>没有 OOS 的版本显示“等待结果”，不再用 +0.000% 冒充成绩。</span></div></div>
    {identities.map(name => {
      const rows = models.filter(row => row.model_identity === name).sort((a, b) => a.lifecycle_status === "LATEST" ? 1 : b.lifecycle_status === "LATEST" ? -1 : 0);
      return <article key={name}><h3>{LABELS[name] ?? name}</h3><div>{rows.map(row => <section key={row.model_version} className={row.lifecycle_status === "LATEST" ? "is-latest" : ""}>
        <span>{row.lifecycle_status === "LATEST" ? "最新版" : "前一版"}</span>
        {row.subsequent_oos_rows ? <><strong>{pct(row.cumulative_quote_return)}</strong><small>{row.subsequent_oos_rows} 条 · {row.distinct_days} 日 · PF {row.profit_factor_quote_adjusted?.toFixed(2) ?? "—"}</small><small>出方向 {(100 * (row.coverage_rate ?? 0)).toFixed(1)}% · 平均机会损失 {pct(row.average_oracle_regret ?? 0)}</small></> : <><strong className="pending-score">等待结果</strong><small>尚无创建后30分钟成熟样本</small></>}
      </section>)}</div></article>;
    })}
  </div>;
}

function MarketChart({ market, identity, setIdentity }: { market?: { candles: Candle[]; decisions: Decision[]; training_markers: TrainingMarker[] }; identity: string; setIdentity: (value: string) => void }) {
  const [hours, setHours] = useState(3);
  const [showLong, setShowLong] = useState(true);
  const [showShort, setShowShort] = useState(true);
  const [showWait, setShowWait] = useState(false);
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
    let nextEligible = Number.NEGATIVE_INFINITY;
    return candidateDecisions.filter(row => {
      const time = Date.parse(row.decision_time);
      if (time < nextEligible) return false;
      nextEligible = time + 1_800_000;
      return true;
    });
  }, [candidateDecisions, dense]);
  if (!candles.length) return <Empty text="最近24小时还没有可绘制的本机 Bid/Ask 报价。" />;
  const low = Math.min(...candles.map(row => row.low)); const high = Math.max(...candles.map(row => row.high));
  const start = Date.parse(candles[0].time); const end = Date.parse(candles.at(-1)!.time) + 300_000;
  const xTime = (time: string) => 55 + (Date.parse(time) - start) / Math.max(1, end - start) * 870;
  const y = (value: number) => 24 + (high - value) / Math.max(.00001, high - low) * 320;
  const byTime = (time: string) => candles.reduce((best, row) => Math.abs(Date.parse(row.time) - Date.parse(time)) < Math.abs(Date.parse(best.time) - Date.parse(time)) ? row : best, candles[0]);
  const hiddenCount = candidateDecisions.length - decisions.length;
  const counts = scopedDecisions.reduce((total, row) => ({ ...total, [row.recommended_action]: total[row.recommended_action] + 1 }), { LONG: 0, SHORT: 0, WAIT: 0 } as Record<string, number>);
  const unhealthyWaits = scopedDecisions.filter(row => row.recommended_action === "WAIT" && row.prediction_status === "DATA_UNHEALTHY").length;
  const selectedX = selected ? xTime(selected.decision_time) : null;
  const selectedExitX = selected ? Math.min(925, xTime(selected.exit_time)) : null;
  const timeLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const resultLabel = (value: number | null) => value == null ? "等待30分钟结果" : pct(value);
  return <div className="chart-block market-chart-block">
    <div className="chart-caption"><div><b>每根K线5分钟 · 每个箭头预测未来30分钟</b><span>不是预测未来5分钟。系统每5分钟重新判断一次，箭头的成绩统一在30分钟后结算；不代表真实下单。</span></div><select value={identity} onChange={event => { setIdentity(event.target.value); setSelected(null); }}>{Object.entries(LABELS).filter(([key]) => key !== "CHAMPION_0").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
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
      {candles.map((row, index) => { const cx = 55 + index / Math.max(1, candles.length - 1) * 870; const width = Math.max(1.5, 650 / candles.length); const up = row.close >= row.open; return <g key={row.time}><line x1={cx} x2={cx} y1={y(row.high)} y2={y(row.low)} stroke={up ? "#476b19" : "#c9362b"} /><rect x={cx - width / 2} width={width} y={Math.min(y(row.open), y(row.close))} height={Math.max(1, Math.abs(y(row.open) - y(row.close)))} fill={up ? "#476b19" : "#c9362b"} /></g>; })}
      {selectedX != null && selectedExitX != null && <g className="selected-window"><rect x={selectedX} width={Math.max(2, selectedExitX-selectedX)} y="18" height="332" /><line x1={selectedX} x2={selectedX} y1="18" y2="350" /><line x1={selectedExitX} x2={selectedExitX} y1="18" y2="350" /><text x={selectedX+4} y="32">预测</text><text x={Math.max(selectedX+36, selectedExitX-58)} y="32">30分钟后</text></g>}
      {decisions.map(row => { const candle = byTime(row.decision_time); const cx = xTime(row.decision_time); const action = row.recommended_action; const cy = action === "LONG" ? y(candle.low) + 12 : action === "SHORT" ? y(candle.high) - 12 : y(candle.close); const color = action === "LONG" ? "#476b19" : action === "SHORT" ? "#c9362b" : "#777267"; const isSelected = selected?.source_decision_id === row.source_decision_id && selected?.model_identity === row.model_identity; return <g key={`${row.source_decision_id}-${row.model_identity}`} role="button" tabIndex={0} className={`decision-marker${isSelected ? " selected" : ""}`} onClick={() => setSelected(row)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") setSelected(row); }}><title>{`${timeLabel(row.decision_time)} · ${action} · 点击查看30分钟结果`}</title>{isSelected && <circle cx={cx} cy={cy} r="11" fill="none" stroke={color} strokeWidth="2" />}{action === "WAIT" ? <circle cx={cx} cy={cy} r="5" fill={color} /> : <path d={action === "LONG" ? `M ${cx} ${cy-7} l -6 11 h 12 z` : `M ${cx} ${cy+7} l -6 -11 h 12 z`} fill={color} />}</g>; })}
      {showTraining && (market?.training_markers ?? []).filter(row => row.model_identity === identity).map(row => <g key={row.model_version}><line x1={xTime(row.created_at)} x2={xTime(row.created_at)} y1="18" y2="350" className="training-line" /><text x={xTime(row.created_at)+4} y="347" className="training-label">新模型</text></g>)}
      <text x="5" y="30">{high.toFixed(2)}</text><text x="5" y="350">{low.toFixed(2)}</text>
    </svg>
    <div className="chart-legend"><span><i className="long-dot" />看多预测</span><span><i className="short-dot" />看空预测</span>{showWait && <span><i className="wait-dot" />等待，不持仓</span>}{showTraining && <span><i className="train-dot" />模型换版</span>}</div>
    <div className="decision-reader" aria-live="polite">{selected ? <>
      <div><small>一次完整观察</small><strong>{timeLabel(selected.decision_time)} 预测 {selected.recommended_action}</strong><span>→ {timeLabel(selected.exit_time)} 固定观察结果</span></div>
      <DecisionPayoff selected={selected} resultLabel={resultLabel} />
    </> : <><div><small>怎样阅读</small><strong>点击图中的三角形</strong><span>这里只显示一次预测；选中后才标出它对应的30分钟观察窗口。</span></div></>}</div>
    <p className="wait-explainer"><b>WAIT 怎样产生：</b> Ridge 学习的是未来30分钟连续收益，不是硬猜三分类。Long、Wait=0、Short 三种结果都会被评分；只有最佳方向扣除 Bid/Ask 成本与95%不确定性后仍为正，才显示方向，否则 WAIT。系统同时记录 WAIT 错过的事后机会，但不会用惩罚强迫开仓。</p>
  </div>;
}

function DecisionPayoff({ selected, resultLabel }: { selected: Decision; resultLabel: (value: number | null) => string }) {
  if (selected.outcome_status !== "VALID") return <div><small>三种动作同场评分</small><strong>等待30分钟结果</strong><span>成熟后显示 Long / Wait / Short 的成本后百分比。</span></div>;
  const longValue = selected.long_quote_return ?? 0;
  const shortValue = selected.short_quote_return ?? 0;
  const choices = [{ name: "LONG", value: longValue }, { name: "WAIT", value: 0 }, { name: "SHORT", value: shortValue }];
  const best = choices.reduce((winner, choice) => choice.value > winner.value ? choice : winner);
  const policy = selected.recommended_action === "LONG" ? longValue : selected.recommended_action === "SHORT" ? shortValue : 0;
  const regret = best.value - policy;
  return <div><small>三种动作同一30分钟结果</small><strong>Long {resultLabel(longValue)} · Wait ±0.000% · Short {resultLabel(shortValue)}</strong><span>事后最佳 {best.name} · 本次建议 {selected.recommended_action} · 机会损失 {pct(regret)}</span></div>;
}

function Empty({ text }: { text: string }) { return <div className="graph-empty"><strong>等待可验证数据</strong><p>{text}</p></div>; }
