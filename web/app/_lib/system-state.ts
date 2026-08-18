export type DataReadState = "CURRENT" | "REFRESHING" | "STALE_SNAPSHOT" | "UNAVAILABLE";
export type LiveMarketState = "LIVE" | "MARKET_CLOSED" | "MARKET_DATA_UNAVAILABLE";
export type OperationalState = "HEALTHY" | "WARNING" | "ERROR";
export type MarketSession = "OPEN" | "CLOSED" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE";

export type SystemStateInput = {
  loading: boolean;
  error: boolean;
  hasSnapshot: boolean;
  online: boolean;
  marketSession?: MarketSession;
  operationalStatus?: OperationalState;
};

export function systemStateAxes(state: SystemStateInput) {
  const readState: DataReadState = state.error
    ? state.hasSnapshot ? "STALE_SNAPSHOT" : "UNAVAILABLE"
    : state.loading ? "REFRESHING" : "CURRENT";
  const liveMarketState: LiveMarketState = (
    state.marketSession === "CLOSED" || state.marketSession === "WEEKLY_CLOSED"
  ) ? "MARKET_CLOSED" : state.online ? "LIVE" : "MARKET_DATA_UNAVAILABLE";
  return {
    readState,
    liveMarketState,
    operationalState: (state.operationalStatus ?? "HEALTHY") as OperationalState,
  };
}

export function systemStatePresentation(state: SystemStateInput) {
  const axes = systemStateAxes(state);
  if (axes.readState === "UNAVAILABLE") {
    return { ...axes, label: "状态不可用", tone: "is-down" };
  }
  if (axes.readState === "REFRESHING" && !state.hasSnapshot) {
    return { ...axes, label: "连接中", tone: "is-loading" };
  }
  if (axes.readState === "STALE_SNAPSHOT") {
    return { ...axes, label: "状态更新失败", tone: "is-loading" };
  }
  if (axes.operationalState === "ERROR") {
    return { ...axes, label: "运行异常", tone: "is-down" };
  }
  if (axes.operationalState === "WARNING") {
    return { ...axes, label: "运行警告", tone: "is-loading" };
  }
  if (axes.liveMarketState === "MARKET_CLOSED") {
    return { ...axes, label: "市场休市", tone: "is-live" };
  }
  if (axes.liveMarketState === "LIVE") {
    return { ...axes, label: "实时链路正常", tone: "is-live" };
  }
  return { ...axes, label: "实时链路不可用", tone: "is-loading" };
}
