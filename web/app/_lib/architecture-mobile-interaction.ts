export type ArchitectureMobilePanel = "NONE" | "INSPECTOR" | "ADVANCED";

export type ArchitectureMobileInteractionState = {
  activePathNodeId: string | null;
  inspectorNodeId: string | null;
  inspectorOpen: boolean;
  advancedOpen: boolean;
  mobilePanel: ArchitectureMobilePanel;
};

export type ArchitectureMobileInteractionEvent =
  | { type: "NODE_TAP"; nodeId: string }
  | { type: "OPEN_INSPECTOR"; nodeId?: string }
  | { type: "CLOSE_INSPECTOR" }
  | { type: "CLEAR_PATH" }
  | { type: "OPEN_ADVANCED" }
  | { type: "CLOSE_ADVANCED" }
  | { type: "CHANGE_VIEW" }
  | { type: "OPEN_SUBSYSTEM" }
  | { type: "SELECT_SEARCH_RESULT"; nodeId: string }
  | { type: "START_SCENARIO" }
  | { type: "MOVE_SCENARIO" }
  | { type: "CLOSE_SCENARIO" }
  | { type: "SWITCH_MODE" }
  | { type: "BACKDROP_CLICK" }
  | { type: "ESCAPE" }
  | { type: "SELECT_ADVANCED_DESTINATION" };

export const INITIAL_ARCHITECTURE_MOBILE_INTERACTION: ArchitectureMobileInteractionState = {
  activePathNodeId: null,
  inspectorNodeId: null,
  inspectorOpen: false,
  advancedOpen: false,
  mobilePanel: "NONE",
};

function closePanel(state: ArchitectureMobileInteractionState): ArchitectureMobileInteractionState {
  return {
    ...state,
    inspectorNodeId: null,
    inspectorOpen: false,
    advancedOpen: false,
    mobilePanel: "NONE",
  };
}

function resetForBoundary(state: ArchitectureMobileInteractionState): ArchitectureMobileInteractionState {
  return {
    ...closePanel(state),
    activePathNodeId: null,
  };
}

export function architectureMobileInteractionReducer(
  state: ArchitectureMobileInteractionState,
  event: ArchitectureMobileInteractionEvent,
): ArchitectureMobileInteractionState {
  switch (event.type) {
    case "NODE_TAP":
    case "SELECT_SEARCH_RESULT":
      return { ...closePanel(state), activePathNodeId: event.nodeId };
    case "OPEN_INSPECTOR": {
      const inspectorNodeId = event.nodeId ?? state.activePathNodeId;
      if (!inspectorNodeId) return state;
      return { ...state, inspectorNodeId, inspectorOpen: true, advancedOpen: false, mobilePanel: "INSPECTOR" };
    }
    case "CLOSE_INSPECTOR":
      return state.mobilePanel === "INSPECTOR" ? closePanel(state) : state;
    case "CLEAR_PATH":
      return { ...closePanel(state), activePathNodeId: null };
    case "OPEN_ADVANCED":
      return { ...state, inspectorNodeId: null, inspectorOpen: false, advancedOpen: true, mobilePanel: "ADVANCED" };
    case "CLOSE_ADVANCED":
    case "SELECT_ADVANCED_DESTINATION":
      return state.mobilePanel === "ADVANCED" ? closePanel(state) : state;
    case "BACKDROP_CLICK":
    case "ESCAPE":
      return closePanel(state);
    case "CHANGE_VIEW":
    case "OPEN_SUBSYSTEM":
    case "SWITCH_MODE":
      return resetForBoundary(state);
    case "START_SCENARIO":
    case "MOVE_SCENARIO":
    case "CLOSE_SCENARIO":
      return state;
  }
}

export function architectureMobileInteractionIsValid(state: ArchitectureMobileInteractionState) {
  return !(state.inspectorOpen && state.advancedOpen)
    && state.inspectorOpen === (state.mobilePanel === "INSPECTOR")
    && state.advancedOpen === (state.mobilePanel === "ADVANCED")
    && (!state.inspectorOpen || Boolean(state.inspectorNodeId))
    && (state.inspectorOpen || state.inspectorNodeId === null);
}

export function architectureSheetTabIndex(currentIndex: number, direction: 1 | -1, count: number) {
  if (count <= 0) return -1;
  return (currentIndex + direction + count) % count;
}

type ArchitectureScrollStyle = { overflow: string; position: string; top: string; width: string };

export function lockArchitecturePageScroll(style: ArchitectureScrollStyle, scrollY: number) {
  const previous = { overflow: style.overflow, position: style.position, top: style.top, width: style.width };
  style.overflow = "hidden";
  style.position = "fixed";
  style.top = `-${scrollY}px`;
  style.width = "100%";
  return { previous, scrollY };
}

export function restoreArchitecturePageScroll(style: ArchitectureScrollStyle, lock: ReturnType<typeof lockArchitecturePageScroll>) {
  Object.assign(style, lock.previous);
  return lock.scrollY;
}
