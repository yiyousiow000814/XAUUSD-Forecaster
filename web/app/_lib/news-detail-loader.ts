import { loadDashboardResource } from "./dashboard-resource";
import { newsArticleDetailUrl, type NewsArticleReference } from "./news-article-reference";

export type NewsDetailTarget = { detail_key: string; source?: string; source_item_id?: string; revision_number?: number; cluster_id?: string } | NewsArticleReference;
export type NewsDetail = { detail_key?: string; payload: Record<string, unknown> };
const cache = new Map<string, NewsDetail>();
const aliases = new Map<string, { key: string; until: number }>();
const pending = new Map<string, Promise<NewsDetail>>();
type Job = { target: NewsDetailTarget; resolve: (value: NewsDetail) => void; reject: (error: unknown) => void };
const queue: Job[] = [];
let active = 0;
const identity = (target: NewsDetailTarget) => "detail_key" in target
  ? target.detail_key : JSON.stringify([target.source, target.source_item_id, target.revision_number, target.cluster_id, target.content_hash]);
function remember(target: NewsDetailTarget, value: NewsDetail) {
  const key = "detail_key" in target ? target.detail_key : value.detail_key;
  if (!key) throw new Error("新闻详情缺少身份编号");
  value = { ...value, detail_key: key, payload: { ...target, ...value.payload } };
  cache.set(key, value);
  while (cache.size > 128) cache.delete(cache.keys().next().value!);
  const article = "detail_key" in target
    ? { ...target, content_hash: value.payload.content_hash } : target;
  if (article.source && article.source_item_id && article.revision_number && article.cluster_id && typeof article.content_hash === "string") {
    aliases.set(identity({source: article.source, source_item_id: article.source_item_id, revision_number: article.revision_number, cluster_id: article.cluster_id, content_hash: article.content_hash}), {key, until: Date.now() + 15_000});
    while (aliases.size > 128) aliases.delete(aliases.keys().next().value!);
  }
  return value;
}
export function readNewsDetail(target: NewsDetailTarget): NewsDetail | null {
  if ("detail_key" in target) return cache.get(target.detail_key) ?? null;
  const alias = aliases.get(identity(target));
  return alias && alias.until > Date.now() ? cache.get(alias.key) ?? null : null;
}
async function drain() {
  if (active >= 2 || !queue.length) return;
  const first = queue.shift()!;
  const jobs = [first];
  if ("detail_key" in first.target) {
    for (let i = 0; i < queue.length && jobs.length < 12;) {
      if ("detail_key" in queue[i].target) jobs.push(...queue.splice(i, 1));
      else i++;
    }
  }
  active++;
  void drain();
  try {
    if ("detail_key" in first.target) {
      const keys = jobs.map(job => (job.target as {detail_key:string}).detail_key);
      const body = await loadDashboardResource<{items: Record<string, NewsDetail>}>(
        `/api/news-content?keys=${encodeURIComponent(keys.join(","))}`, {maxAgeMs: 0});
      for (const job of jobs) {
        const key = (job.target as {detail_key:string}).detail_key;
        const item = body.items[key];
        if (item) job.resolve(remember(job.target, item));
        else job.reject(new Error("原文详情暂时无法读取"));
      }
    } else {
      const body = await loadDashboardResource<NewsDetail>(newsArticleDetailUrl(first.target), {maxAgeMs: 0});
      first.resolve(remember(first.target, body));
    }
  } catch (error) { jobs.forEach(job => job.reject(error)); }
  finally { active--; void drain(); }
}
export function loadNewsDetail(target: NewsDetailTarget): Promise<NewsDetail> {
  const cached = readNewsDetail(target);
  if (cached) return Promise.resolve(cached);
  const id = identity(target);
  const existing = pending.get(id);
  if (existing) return existing;
  const promise = new Promise<NewsDetail>((resolve, reject) => queue.push({target, resolve, reject}));
  pending.set(id, promise);
  const clear = () => pending.delete(id);
  void promise.then(clear, clear);
  queueMicrotask(() => void drain());
  return promise;
}
