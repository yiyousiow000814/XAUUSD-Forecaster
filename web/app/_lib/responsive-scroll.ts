export const isPhoneViewport = () => window.matchMedia("(max-width: 850px)").matches;

export const settleResponsiveScroll = (
  scroll: (options: ScrollToOptions) => void,
  readTop: () => number,
  desktopTop: number,
) => {
  if (isPhoneViewport()) {
    scroll({ top: 0, left: 0, behavior: "instant" });
    return;
  }
  // Lazy views may commit a short Suspense shell before their full content.
  // Retry only while the browser has clamped the requested desktop position;
  // stop immediately once the original reading position is reachable.
  let remainingFrames = 30;
  const settle = () => {
    scroll({ top: desktopTop, left: 0, behavior: "instant" });
    remainingFrames -= 1;
    if (Math.abs(readTop() - desktopTop) > 1 && remainingFrames > 0) {
      window.requestAnimationFrame(settle);
    }
  };
  window.requestAnimationFrame(settle);
};
