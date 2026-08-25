export type ArchitectureCameraIntent =
  | { type: "FIT_VIEW"; viewId: string }
  | { type: "FOCUS_NODE"; viewId: string; nodeId: string; source: "NODE_TAP" | "INSPECTOR" | "SEARCH" | "SCENARIO_STEP" }
  | { type: "REFIT_AFTER_INSPECTOR_CLOSE"; viewId: string }
  | { type: "MANUAL_FIT"; viewId: string };

export type ArchitectureCameraLayout = {
  viewId: string;
  nodesInitialized: boolean;
  flowInitialized: boolean;
  canvasTransitionComplete: boolean;
  width: number;
  height: number;
};

type CameraControllerOptions = {
  requestFrame: (callback: FrameRequestCallback) => number;
  cancelFrame: (frame: number) => void;
  readLayout: () => ArchitectureCameraLayout;
  execute: (intent: ArchitectureCameraIntent) => void;
};

export function createArchitectureCameraController(initialOptions?: CameraControllerOptions) {
  let options = initialOptions;
  let pending: ArchitectureCameraIntent | null = null;
  let frame: number | null = null;
  let stableLayout = "";

  const cancelScheduledFrame = () => {
    if (frame === null) return;
    options?.cancelFrame(frame);
    frame = null;
  };

  const schedule = () => {
    const environment = options;
    if (!environment || !pending || frame !== null) return;
    frame = environment.requestFrame(() => {
      frame = null;
      if (!pending) return;
      const layout = environment.readLayout();
      if (layout.viewId !== pending.viewId || !layout.nodesInitialized || !layout.flowInitialized
          || !layout.canvasTransitionComplete || layout.width <= 0 || layout.height <= 0) {
        stableLayout = "";
        return;
      }
      const signature = `${layout.viewId}:${layout.width}:${layout.height}`;
      if (signature !== stableLayout) {
        stableLayout = signature;
        schedule();
        return;
      }
      const current = pending;
      pending = null;
      stableLayout = "";
      environment.execute(current);
    });
  };

  return {
    configure(nextOptions: CameraControllerOptions) {
      options = nextOptions;
      schedule();
    },
    request(intent: ArchitectureCameraIntent) {
      cancelScheduledFrame();
      pending = intent;
      stableLayout = "";
      schedule();
    },
    layoutChanged() {
      if (!pending) return;
      cancelScheduledFrame();
      stableLayout = "";
      schedule();
    },
    cancel() {
      cancelScheduledFrame();
      pending = null;
      stableLayout = "";
    },
    pendingIntent() {
      return pending;
    },
  };
}
