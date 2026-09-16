export type MapSource = { path: string; witness: string };
export type MapNode = { id: string; title: string; detail: string; source: MapSource; child?: string };
export type MapEdge = { from: string; to: string; label: string; source: MapSource };
export type SystemMap = { id: string; title: string; summary: string; nodes: MapNode[]; edges: MapEdge[] };
const source = (path: string, witness: string): MapSource => ({ path, witness });
const collector = source('scripts/runtime/run_forward_collector.py', 'engine = ForwardEngine(ledger, provider');
const engine = source('xauusd_forecaster/decision/engine.py', 'self.ledger.append_decision(decision_record');
const news = source('xauusd_forecaster/news/collection/runtime.py', 'ForwardEngine(ledger, NullMarketProvider()).collect_news');
const annotation = source('scripts/runtime/run_news_annotator.py', 'statuses = run_scheduled_batch_with_lock_retry(');
const training = source('xauusd_forecaster/training/runtime.py', 'train_due_v2(ledger, cutoff, self.model_root)');
const generation = source('xauusd_forecaster/training/generation.py', 'INSERT INTO news_model_generation_activations_v1');
const api = source('scripts/runtime/run_dashboard_api.py', 'read_model_owner.start()');
const sync = source('scripts/runtime/run_dashboard_sync.py', '_post_json(target["remote_ingest_url"], live_payload, target)');
const worker = source('web/worker/api-router.ts', 'return publicStatusRead(env.DB)');
const bridge = source('ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs', 'double bid = this.Symbol.Bid');
const market = source('scripts/runtime/run_forward_collector.py', 'JsonlMarketProvider(quote_path)');
const node = (id: string, title: string, detail: string, ref: MapSource, child?: string): MapNode => ({ id, title, detail, source: ref, child });
const edge = (from: string, to: string, label: string, ref: MapSource): MapEdge => ({ from, to, label, source: ref });

// Reviewed configured paths. These references establish source wiring, never live health.
export const SYSTEM_MAPS: SystemMap[] = [
  { id: 'system', title: '系统总览', summary: '点击模块展开流程；箭头表示数据或调用方向。', nodes: [
    node('quotes', '行情采集', 'cTrader · Bid / Ask', bridge, 'quotes'),
    node('news', '新闻处理', '采集、语义与影响复核', news, 'news'),
    node('decision', '预测与记录', '每 5 分钟 · Long / Short / Wait', engine, 'decision'),
    node('training', '模型训练', '结果评分 → 完整模型代际', training, 'training'),
    node('dashboard', '网页与同步', '本机 SQLite → Cloudflare', sync, 'dashboard'),
  ], edges: [
    edge('quotes', 'decision', '行情快照', collector),
    edge('news', 'decision', '决策时可见的新闻', source('xauusd_forecaster/decision/engine.py', 'self.ledger.visible_news(decision_time)')),
    edge('decision', 'training', '到期结果与训练请求', source('scripts/runtime/run_forward_collector.py', 'training_owner.wake()')),
    edge('decision', 'dashboard', '本地证据与状态', api),
  ] },
  { id: 'quotes', title: '行情采集', summary: '保留原始报价与市场状态，预测只读取当时可见的数据。', nodes: [
    node('broker', 'cTrader 报价', '读取经纪商 Bid / Ask', bridge),
    node('files', '本机行情文件', 'UTC 日分区与市场状态心跳', source('ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs', 'this.WriteMarketSession()')),
    node('provider', '行情窗口', 'JsonlMarketProvider 读取报价', market),
    node('forecast', '预测引擎', '冻结决策时点快照', engine, 'decision'),
  ], edges: [edge('broker','files','写入报价',source('ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs', "this.writer.WriteLine('}')")),edge('files','provider','读取报价窗口',market),edge('provider','forecast','传入市场数据',collector)] },
  { id: 'news', title: '新闻处理', summary: '采集和 AI 复核独立运行；预测读取已经入库的可见结果。', nodes: [
    node('sources','新闻来源','按来源周期采集',news),
    node('intake','原文与修订','保存来源内容和时间证据',source('xauusd_forecaster/decision/engine.py','collect_official_news(self.ledger, now)')),
    node('annotator','AI 复核任务','独立进程执行语义任务',annotation),
    node('visible','新闻证据','有效结果供后续决策读取',source('xauusd_forecaster/decision/engine.py','self.ledger.visible_news(decision_time)')),
  ], edges: [edge('sources','intake','采集入库',news),edge('intake','annotator','待处理新闻',annotation),edge('annotator','visible','复核结果入库',source('xauusd_forecaster/news/annotation/product.py','ledger.append_annotation('))] },
  { id: 'decision', title: '预测与记录', summary: '固定决策时钟，保存不可改写的历史；30 分钟后记录结果。', nodes: [
    node('clock','五分钟时钟','Collector 驱动决策',collector),
    node('snapshot','时点快照','冻结行情、新闻和模型身份',source('xauusd_forecaster/decision/engine.py','snapshot = build_forward_snapshot(')),
    node('infer','生成预测','Long / Short / Wait',source('xauusd_forecaster/decision/engine.py','predictions = build_shadow_predictions')),
    node('ledger','本机 SQLite','追加决策和预测证据',engine),
    node('outcome','30 分钟结果','到期后计算并追加结果',source('scripts/runtime/run_forward_collector.py','engine.settle_due_outcomes(now)')),
  ], edges: [edge('clock','snapshot','创建决策快照',source('xauusd_forecaster/decision/engine.py','snapshot = build_forward_snapshot(')),edge('snapshot','infer','读取冻结输入',source('xauusd_forecaster/decision/engine.py','predictions = build_shadow_predictions(self.ledger, snapshot, decision_time)')),edge('infer','ledger','追加记录',engine),edge('ledger','outcome','结算到期决策',source('xauusd_forecaster/decision/engine.py','def settle_due_outcomes('))] },
  { id: 'training', title: '模型训练', summary: '训练由独立后台线程处理，失败时保留上一完整代际。', nodes: [
    node('results','结果与评分','Collector 完成到期结算',source('scripts/runtime/run_forward_collector.py','completed_outcomes = engine.settle_due_outcomes(now)')),
    node('owner','后台训练','按请求唤醒独立训练 owner',source('scripts/runtime/run_forward_collector.py','training_owner = BackgroundTrainingOwner(')),
    node('train','训练到期模型','读取有界材料并训练',training),
    node('activate','发布完整代际','完整模型集合原子激活',generation),
  ], edges: [edge('results','owner','唤醒训练',source('scripts/runtime/run_forward_collector.py','training_owner.wake()')),edge('owner','train','运行到期训练',training),edge('train','activate','完整代际发布',generation)] },
  { id: 'dashboard', title: '网页与同步', summary: 'SQLite 是预测证据权威；D1 提供网页投影，浏览器不连接本机。', nodes: [
    node('local','本机 SQLite','预测、新闻和评分证据',source('scripts/runtime/run_dashboard_api.py','DashboardReadModelOwner(')),
    node('read','读模型 / 本机 API','独立生成有界页面资源',api),
    node('sync','Dashboard Sync','心跳与大资源分开传输',sync),
    node('d1','Cloudflare D1','经过身份验证的同步写入',source('web/worker/api-router.ts','if (pathname === "/api/ingest")')),
    node('web','Worker 与浏览器','读取投影，呈现页面',worker),
  ], edges: [edge('local','read','生成读模型',source('scripts/runtime/run_dashboard_api.py','read_model_owner = DashboardReadModelOwner(')),edge('read','sync','本机 HTTP 读取',source('scripts/runtime/run_dashboard_sync.py','_local_critical_status_url(config)')),edge('sync','d1','认证同步',sync),edge('d1','web','查询页面数据',worker)] },
];
export const systemMap = (id: string) => SYSTEM_MAPS.find(view => view.id === id) ?? SYSTEM_MAPS[0];

export function mapRanks(view: SystemMap): MapNode[][] {
  const ranks = new Map<string, number>();
  for (let pass = 0; pass < view.nodes.length; pass++) {
    for (const node of view.nodes) {
      const parents = view.edges.filter(edge => edge.to === node.id);
      ranks.set(node.id, parents.length ? Math.max(...parents.map(edge => (ranks.get(edge.from) ?? 0) + 1)) : 0);
    }
  }
  return [...new Set(ranks.values())].sort((a, b) => a - b).map(rank => view.nodes.filter(node => ranks.get(node.id) === rank));
}

