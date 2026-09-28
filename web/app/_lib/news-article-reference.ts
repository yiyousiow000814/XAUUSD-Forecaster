export type NewsArticleReference = {
  source: string;
  source_item_id: string;
  revision_number: number;
  cluster_id: string;
  content_hash: string;
};

export function parseNewsArticleReference(raw: string | null): NewsArticleReference | null {
  if (!raw || raw.length > 6000) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    for (const [key, limit] of [["source", 128], ["source_item_id", 4096], ["cluster_id", 256]] as const) {
      if (typeof value[key] !== "string" || !value[key].trim() || value[key].length > limit) return null;
    }
    if (!Number.isSafeInteger(value.revision_number) || value.revision_number < 1
      || typeof value.content_hash !== "string" || !/^[a-f0-9]{64}$/.test(value.content_hash)) return null;
    return {source:value.source, source_item_id:value.source_item_id,
      revision_number:value.revision_number, cluster_id:value.cluster_id, content_hash:value.content_hash};
  } catch { return null; }
}

export function newsArticleDetailUrl(article: NewsArticleReference): string {
  return `/api/news-content?article=${encodeURIComponent(JSON.stringify(article))}`;
}
