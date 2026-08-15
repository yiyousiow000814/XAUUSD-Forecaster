export const isPhoneViewport = () => window.matchMedia("(max-width: 850px)").matches;

export const settleResponsiveScroll = (
  scroll: (options: ScrollToOptions) => void,
  desktopTop: number,
) => {
  const top = isPhoneViewport() ? 0 : desktopTop;
  scroll({ top, left: 0, behavior: "instant" });
};
