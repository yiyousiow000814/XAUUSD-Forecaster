type CacheEntry = {
  data?: unknown;
  updatedAt: number;
  pending?: Promise<unknown>;
};

const resources = new Map<string, CacheEntry>();
const DEFAULT_MAX_AGE_MS = 15_000;

export function readDashboardResource<T>(url: string): T | null {
  return (resources.get(url)?.data as T | undefined) ?? null;
}

export async function loadDashboardResource<T>(
  url: string,
  options: { force?: boolean; maxAgeMs?: number } = {},
): Promise<T> {
  const entry = resources.get(url) ?? { updatedAt: 0 };
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const isFresh = entry.data !== undefined && Date.now() - entry.updatedAt < maxAgeMs;

  if (!options.force && isFresh) return entry.data as T;
  if (entry.pending) return entry.pending as Promise<T>;

  const pending = fetch(url, { cache: "no-store" }).then(async response => {
    const serialized = await response.text();
    let body: unknown;
    try {
      body = serialized ? JSON.parse(serialized) : null;
    } catch {
      throw new Error(response.ok
        ? "数据服务正在更新，页面会自动重试"
        : `数据服务暂时不可用（HTTP ${response.status}），页面会自动重试`);
    }
    if (!response.ok) {
      const message = body && typeof body === "object" && "error" in body
        ? String(body.error)
        : `HTTP ${response.status}`;
      throw new Error(message);
    }
    resources.set(url, { data: body, updatedAt: Date.now() });
    return body as T;
  }).catch(reason => {
    resources.set(url, { ...entry, pending: undefined });
    throw reason;
  });

  resources.set(url, { ...entry, pending });
  return pending;
}
