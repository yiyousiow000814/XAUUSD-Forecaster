import {
  systemStatePresentation,
  type SystemStateInput,
} from "../_lib/system-state";

export default function SystemStatePill(state: SystemStateInput) {
  const presentation = systemStatePresentation(state);
  return <div
    className={`live-pill ${presentation.tone}`}
    data-read-state={presentation.readState}
    data-live-market-state={presentation.liveMarketState}
    data-operational-state={presentation.operationalState}
  >
    <span />{presentation.label}
  </div>;
}
