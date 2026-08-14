export function quoteBridgePresentation(
  status: string | null | undefined,
  marketSession: string | undefined,
) {
  if (marketSession === "CLOSED" || marketSession === "WEEKLY_CLOSED") {
    return { label: "市场休市 · 新闻继续", good: true };
  }
  if (status === "OK") return { label: "本机在线", good: true };
  return { label: "本机中断", good: false };
}
