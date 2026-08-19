type CacheEntry = {
  data?: unknown;
  updatedAt: number;
  pending?: Promise<unknown>;
  error?: Error;
};

export type DashboardResourceState<T> = {
  data: T | null;
  hasSnapshot: boolean;
  loading: boolean;
  error: Error | null;
  updatedAt: number | null;
};

const resources = new Map<string, CacheEntry>();
const resourceListeners = new Map<string, Set<() => void>>();
const DEFAULT_MAX_AGE_MS = 15_000;
const DEFAULT_TIMEOUT_MS = 10_000;

export class DashboardResourceError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    status: number,
    code: string | null,
    details: Record<string, unknown>,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function primeDashboardResources(initial: Record<string, unknown>): void {
  const updatedAt = Date.now();
  for (const [url, data] of Object.entries(initial)) {
    if (!resources.has(url)) resources.set(url, { data, updatedAt });
  }
}

export function readDashboardResource<T>(url: string): T | null {
  return (resources.get(url)?.data as T | undefined) ?? null;
}

export function readDashboardResourceState<T>(url: string): DashboardResourceState<T> {
  const entry = resources.get(url);
  const hasSnapshot = entry?.data !== undefined;
  return {
    data: hasSnapshot ? entry.data as T : null,
    hasSnapshot,
    loading: entry?.pending !== undefined,
    error: entry?.error ?? null,
    updatedAt: hasSnapshot ? entry.updatedAt : null,
  };
}

export function subscribeDashboardResource(url: string, listener: () => void): () => void {
  const listeners = resourceListeners.get(url) ?? new Set<() => void>();
  listeners.add(listener);
  resourceListeners.set(url, listeners);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) resourceListeners.delete(url);
  };
}

function notifyDashboardResource(url: string): void {
  for (const listener of resourceListeners.get(url) ?? []) listener();
}

export async function loadDashboardResource<T>(
  url: string,
  options: { force?: boolean; maxAgeMs?: number; timeoutMs?: number } = {},
): Promise<T> {
  const entry = resources.get(url) ?? { updatedAt: 0 };
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const isFresh = entry.data !== undefined && Date.now() - entry.updatedAt < maxAgeMs;

  if (!options.force && isFresh) return entry.data as T;
  if (entry.pending) return entry.pending as Promise<T>;

  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );
  const pending = (async () => {
    try {
      const response = await fetch(url, { cache: "no-store", signal: controller.signal });
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
        const details = body && typeof body === "object"
          ? body as Record<string, unknown> : {};
        const message = "error" in details
          ? String(details.error)
          : `HTTP ${response.status}`;
        throw new DashboardResourceError(
          message,
          response.status,
          typeof details.error_code === "string" ? details.error_code : null,
          details,
        );
      }
      resources.set(url, { data: body, updatedAt: Date.now() });
      notifyDashboardResource(url);
      return body as T;
    } catch (reason) {
      const error = reason instanceof DOMException && reason.name === "AbortError"
        ? new Error("数据读取超时，页面会自动重试")
        : reason instanceof Error ? reason : new Error(String(reason));
      const current = resources.get(url) ?? entry;
      resources.set(url, { ...current, pending: undefined, error });
      notifyDashboardResource(url);
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  })();

  resources.set(url, { ...entry, pending });
  notifyDashboardResource(url);
  return pending;
}
