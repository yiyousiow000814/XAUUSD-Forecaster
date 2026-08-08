export type MarketSession = "OPEN" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE";

type SystemState = {
  loading: boolean;
  error: boolean;
  online: boolean;
  marketSession?: MarketSession;
};

export function systemStatePresentation(state: SystemState) {
  if (state.loading) return { label: "连接中", tone: "is-loading" };
  if (state.error) return { label: "状态离线", tone: "is-down" };
  if (state.marketSession === "WEEKLY_CLOSED") {
    return { label: "市场休市", tone: "is-live" };
  }
  if (state.online) return { label: "系统在线", tone: "is-live" };
  return { label: "状态离线", tone: "is-down" };
}

export default function SystemStatePill(state: SystemState) {
  const presentation = systemStatePresentation(state);
  return <div className={`live-pill ${presentation.tone}`}>
    <span />{presentation.label}
  </div>;
}
