export const isPhoneViewport = () => window.matchMedia("(max-width: 850px)").matches;

export const settleResponsiveScroll = (
  scroll: (options: ScrollToOptions) => void,
  readTop: () => number,
  desktopTop: number,
): (() => void) => {
  if (isPhoneViewport()) {
    scroll({ top: 0, left: 0, behavior: "instant" });
    return () => undefined;
  }
  // Lazy views may commit a short Suspense shell before their full content.
  // Retry only while the browser has clamped the requested desktop position;
  // require a few stable frames so a late Suspense commit cannot move it again.
  let remainingFrames = 30;
  let stableFrames = 0;
  let frame: number | null = null;
  let cancelled = false;
  const settle = () => {
    if (cancelled) return;
    scroll({ top: desktopTop, left: 0, behavior: "instant" });
    remainingFrames -= 1;
    stableFrames = Math.abs(readTop() - desktopTop) <= 1 ? stableFrames + 1 : 0;
    if (stableFrames < 6 && remainingFrames > 0) {
      frame = window.requestAnimationFrame(settle);
    }
  };
  frame = window.requestAnimationFrame(settle);
  return () => {
    cancelled = true;
    if (frame !== null) window.cancelAnimationFrame(frame);
  };
};
