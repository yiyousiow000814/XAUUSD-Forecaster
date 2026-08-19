export const ADMIN_SESSION_URL = "/admin/api/session";
export const ADMIN_AUTH_COMPLETE_PATH = "/admin/auth-complete";
export const ADMIN_AUTH_MESSAGE_TYPE = "xauusd:admin-auth-complete";
export const ADMIN_AUTH_STATE_EVENT = "xauusd:admin-auth-state";

export type AdminAuthState = "CHECKING" | "AUTHENTICATED" | "ANONYMOUS" | "FORBIDDEN";
export type AdminAuthProbeOutcome = Exclude<AdminAuthState, "CHECKING"> | "UNAVAILABLE";

export function adminAuthStateAfterProbe(
  current: AdminAuthState,
  outcome: AdminAuthProbeOutcome,
): AdminAuthState {
  return outcome === "UNAVAILABLE" ? current : outcome;
}

export async function probeAdminSession(
  fetcher: typeof fetch = fetch,
): Promise<AdminAuthProbeOutcome> {
  try {
    const response = await fetcher(ADMIN_SESSION_URL, {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "manual",
      headers: { Accept: "application/json" },
    });
    const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
    if (response.status === 403) return "FORBIDDEN";
    if (response.status === 401 || response.type === "opaqueredirect" || response.redirected
      || (response.ok && contentType.includes("text/html"))) return "ANONYMOUS";
    if (!response.ok) return "UNAVAILABLE";
    const payload = await response.json() as { authenticated?: unknown };
    return payload.authenticated === true ? "AUTHENTICATED" : "UNAVAILABLE";
  } catch {
    return "UNAVAILABLE";
  }
}

export function reportAdminAuthOutcome(outcome: Exclude<AdminAuthProbeOutcome, "UNAVAILABLE">) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function"
    || typeof CustomEvent === "undefined") return;
  window.dispatchEvent(new CustomEvent(ADMIN_AUTH_STATE_EVENT, { detail: outcome }));
}

export const reportAdminAuthenticationRequired = () => reportAdminAuthOutcome("ANONYMOUS");

export function subscribeAdminAuthOutcomes(
  listener: (outcome: Exclude<AdminAuthProbeOutcome, "UNAVAILABLE">) => void,
) {
  if (typeof window === "undefined") return () => undefined;
  const handle = (event: Event) => {
    const outcome = (event as CustomEvent<AdminAuthProbeOutcome>).detail;
    if (outcome === "AUTHENTICATED" || outcome === "ANONYMOUS" || outcome === "FORBIDDEN") {
      listener(outcome);
    }
  };
  window.addEventListener(ADMIN_AUTH_STATE_EVENT, handle);
  return () => window.removeEventListener(ADMIN_AUTH_STATE_EVENT, handle);
}

export function isTrustedAdminAuthMessage(
  event: Pick<MessageEvent, "origin" | "source" | "data">,
  expectedOrigin: string,
  expectedSource: MessageEventSource | null,
) {
  return event.origin === expectedOrigin
    && event.source === expectedSource
    && event.data?.type === ADMIN_AUTH_MESSAGE_TYPE;
}

export function openAdminAuthPopup(
  openWindow: (url: string, target: string, features: string) => Window | null,
  fallback: () => void,
) {
  const popup = openWindow(
    ADMIN_AUTH_COMPLETE_PATH,
    "xauusd-admin-auth",
    "popup=yes,width=520,height=680,resizable=yes,scrollbars=yes",
  );
  if (!popup) fallback();
  return popup;
}
