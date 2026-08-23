import { updateDashboardResource } from "./dashboard-resource";

declare const __AURUM_DEPLOYMENT__: {
  branch: string;
  commit_sha: string;
  is_preview: boolean;
};

export type LiveSourceMode = "LIVE_PUSH" | "HTTP_FALLBACK" | "STALE";

type LiveState = {
  schema_version: "PUBLIC_LIVE_V1";
  sequence: number;
  generated_at: string;
  source_revision: string;
  market_session: string;
  freshness: { online: boolean; state: string };
  quote: { bid: number; ask: number; spread: number; source_received_time: string };
  forecast: Record<string, unknown>;
  health: { status?: string; alerts?: unknown[] };
  recent_decisions?: unknown[];
};

type Envelope = { type: "FULL_STATE" | "STATE_UPDATE"; sequence?: number; state: LiveState | Partial<LiveState> };
type Listener = (mode: LiveSourceMode) => void;
type Timer = ReturnType<typeof setTimeout>;

const INITIAL_WAIT_MS = 2_500;
const STALE_AFTER_MS = 75_000;
const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000] as const;

function configuredUrl(): string | null {
  const env = (import.meta as ImportMeta & { env?: Record<string, string> }).env;
  const value = (__AURUM_DEPLOYMENT__.is_preview
    ? env?.VITE_LIVE_BROADCAST_PREVIEW_URL
    : env?.VITE_LIVE_BROADCAST_URL)?.trim();
  if (!value || typeof window === "undefined") return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" && url.protocol !== "wss:") return null;
    url.protocol = url.protocol === "https:" ? "wss:" : url.protocol;
    url.pathname = "/subscribe";
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch { return null; }
}

function validState(value: unknown): value is LiveState {
  if (!value || typeof value !== "object") return false;
  const state = value as Partial<LiveState>;
  return state.schema_version === "PUBLIC_LIVE_V1"
    && Number.isSafeInteger(state.sequence) && Number(state.sequence) > 0
    && typeof state.generated_at === "string"
    && typeof state.quote?.source_received_time === "string"
    && Number.isFinite(state.quote?.bid) && Number.isFinite(state.quote?.ask);
}

function applyToStatus(state: LiveState, mode: LiveSourceMode): void {
  updateDashboardResource<Record<string, unknown>>("/api/status", current => {
    const status = current ?? {};
    const system = status.system && typeof status.system === "object"
      ? status.system as Record<string, unknown> : {};
    const latest = status.latest && typeof status.latest === "object"
      ? status.latest as Record<string, unknown> : {};
    return {
      ...status,
      generated_at: state.generated_at,
      system: { ...system, online: state.freshness.online, market_session: state.market_session },
      latest: { ...latest, ...state.quote },
      research_forecast: state.forecast,
      operational_health: state.health,
      ...(state.recent_decisions ? { recent_decisions: state.recent_decisions } : {}),
      live_transport: {
        source_mode: mode,
        schema_version: state.schema_version,
        sequence: state.sequence,
        source_revision: state.source_revision,
        received_at: new Date().toISOString(),
      },
    };
  });
}

export class LiveBroadcastTransport {
  private readonly url: string;
  private readonly socketFactory: (url: string) => WebSocket;
  private readonly random: () => number;
  private socket: WebSocket | null = null;
  private state: LiveState | null = null;
  private mode: LiveSourceMode = "HTTP_FALLBACK";
  private reconnectAttempt = 0;
  private reconnectTimer: Timer | null = null;
  private initialTimer: Timer | null = null;
  private staleTimer: Timer | null = null;
  private stopped = true;
  private readonly listeners = new Set<Listener>();

  constructor(
    url: string,
    socketFactory: (url: string) => WebSocket = value => new WebSocket(value),
    random: () => number = Math.random,
  ) {
    this.url = url;
    this.socketFactory = socketFactory;
    this.random = random;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
    this.initialTimer = setTimeout(() => {
      if (!this.state) this.setMode("HTTP_FALLBACK");
    }, INITIAL_WAIT_MS);
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.initialTimer) clearTimeout(this.initialTimer);
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.socket?.close(1000, "page closed");
    this.socket = null;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.mode);
    return () => this.listeners.delete(listener);
  }

  sourceMode(): LiveSourceMode { return this.mode; }
  healthy(): boolean { return this.mode === "LIVE_PUSH"; }

  private setMode(mode: LiveSourceMode): void {
    if (this.mode === mode) return;
    this.mode = mode;
    updateDashboardResource<Record<string, unknown>>("/api/status", current => {
      const status = current ?? {};
      const live = status.live_transport && typeof status.live_transport === "object"
        ? status.live_transport as Record<string, unknown> : {};
      return { ...status, live_transport: { ...live, source_mode: mode } };
    });
    for (const listener of this.listeners) listener(mode);
  }

  private connect(): void {
    if (this.stopped) return;
    const socket = this.socketFactory(this.url);
    this.socket = socket;
    socket.addEventListener("message", event => this.message(String(event.data)));
    socket.addEventListener("close", () => this.disconnected(socket));
    socket.addEventListener("error", () => socket.close());
  }

  private message(serialized: string): void {
    let envelope: Envelope;
    try { envelope = JSON.parse(serialized) as Envelope; } catch { return; }
    let next: LiveState;
    if (envelope.type === "FULL_STATE" && validState(envelope.state)) {
      next = envelope.state;
    } else if (envelope.type === "STATE_UPDATE" && this.state) {
      const candidate = { ...this.state, ...envelope.state, sequence: envelope.sequence };
      if (!validState(candidate) || candidate.sequence !== this.state.sequence + 1) {
        this.socket?.close(1008, "sequence gap");
        return;
      }
      next = candidate;
    } else return;
    if (this.state && next.sequence <= this.state.sequence) return;
    this.state = next;
    this.reconnectAttempt = 0;
    this.setMode("LIVE_PUSH");
    applyToStatus(next, "LIVE_PUSH");
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.staleTimer = setTimeout(() => {
      this.setMode("STALE");
      this.socket?.close(1000, "stale stream");
    }, STALE_AFTER_MS);
  }

  private disconnected(socket: WebSocket): void {
    if (socket !== this.socket || this.stopped) return;
    this.socket = null;
    this.setMode(this.state ? "STALE" : "HTTP_FALLBACK");
    const base = BACKOFF_MS[Math.min(this.reconnectAttempt, BACKOFF_MS.length - 1)];
    this.reconnectAttempt += 1;
    const delay = Math.round(base * (0.8 + this.random() * 0.4));
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}

let singleton: LiveBroadcastTransport | null = null;

export function liveBroadcastEnabled(): boolean { return configuredUrl() !== null; }

export function liveBroadcastTransport(): LiveBroadcastTransport | null {
  const url = configuredUrl();
  if (!url) return null;
  if (!singleton) singleton = new LiveBroadcastTransport(url);
  return singleton;
}

export function isLiveBroadcastHealthy(): boolean {
  return liveBroadcastTransport()?.healthy() ?? false;
}
