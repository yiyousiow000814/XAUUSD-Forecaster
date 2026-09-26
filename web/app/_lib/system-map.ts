export type MapSource = { path: string; witness: string };
export type MapNode = { id: string; title: string; detail: string; source: MapSource; child?: string };
export type MapEdge = { from: string; to: string; label: string; source: MapSource };
export type SystemMap = { id: string; title: string; summary: string; nodes: MapNode[]; edges: MapEdge[] };
const source = (path: string, witness: string): MapSource => ({ path, witness });
const collector = source('scripts/runtime/run_forward_collector.py', 'news_owner = NewsCollectionOwner(');
const news = source('xauusd_forecaster/news/collection/runtime.py', 'collect_official_news(ledger, observed_at)');
const annotation = source('scripts/runtime/run_news_annotator.py', 'statuses = run_scheduled_batch_with_lock_retry(');
const api = source('scripts/runtime/run_dashboard_api.py', 'read_model_owner.start()');
const sync = source('scripts/runtime/run_dashboard_sync.py', '_post_json(target["remote_ingest_url"], live_payload, target)');
const worker = source('web/worker/api-router.ts', 'return publicStatusRead(env.DB)');
const bridge = source('ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs', 'double bid = this.Symbol.Bid');
const market = source('xauusd_forecaster/dashboard/runtime_status.py', 'def latest_quote(');
const node = (id: string, title: string, detail: string, ref: MapSource, child?: string): MapNode => ({ id, title, detail, source: ref, child });
const edge = (from: string, to: string, label: string, ref: MapSource): MapEdge => ({ from, to, label, source: ref });

// Reviewed configured paths. These references establish source wiring, never live health.
export const SYSTEM_MAPS: SystemMap[] = [
  { id: 'system', title: '系统总览', summary: '点击模块展开流程；箭头表示数据或调用方向。', nodes: [
    node('quotes', '行情采集', 'cTrader · Bid / Ask', bridge, 'quotes'),
    node('news', '新闻处理', '采集、语义与影响复核', news, 'news'),
    node('dashboard', '网页与同步', '本机 SQLite → Cloudflare', sync, 'dashboard'),
  ], edges: [
    edge('quotes', 'dashboard', '行情与市场状态', api),
    edge('news', 'dashboard', '新闻事件与来源证据', api),
  ] },
  { id: 'quotes', title: '行情采集', summary: '保留原始报价与市场状态，由网页读取。', nodes: [
    node('broker', 'cTrader 报价', '读取经纪商 Bid / Ask', bridge),
    node('files', '本机行情文件', 'UTC 日分区与市场状态心跳', source('ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs', 'this.WriteMarketSession()')),
    node('provider', '当前报价', '读取报价文件', market),
    node('display', '网页行情', '展示最新报价', api, 'dashboard'),
  ], edges: [edge('broker','files','写入报价',source('ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs', "this.writer.WriteLine('}')")),edge('files','provider','读取报价窗口',market),edge('provider','display','展示行情',api)] },
  { id: 'news', title: '新闻处理', summary: '采集和 AI 复核独立运行，形成可追溯的新闻事件。', nodes: [
    node('sources','新闻来源','按来源周期采集',news),
    node('intake','原文与修订','保存来源内容和时间证据',news),
    node('annotator','AI 复核任务','独立进程执行语义任务',annotation),
    node('visible','新闻证据','当前可用的事件与来源',source('xauusd_forecaster/news/semantics/evidence.py','def event_evidence_rows_from_connection(')),
  ], edges: [edge('sources','intake','采集入库',news),edge('intake','annotator','待处理新闻',annotation),edge('annotator','visible','复核结果入库',source('xauusd_forecaster/news/annotation/product.py','ledger.append_annotation('))] },
  { id: 'dashboard', title: '网页与同步', summary: 'SQLite 是新闻证据权威；D1 提供网页投影，浏览器不连接本机。', nodes: [
    node('local','本机 SQLite','新闻、事件和来源证据',source('scripts/runtime/run_dashboard_api.py','DashboardReadModelOwner(')),
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
