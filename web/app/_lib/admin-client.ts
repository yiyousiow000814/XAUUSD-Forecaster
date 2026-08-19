import { reportAdminAuthOutcome } from "./admin-auth-session";

export const ADMIN_AUTH_REQUIRED_MESSAGE = "需要管理员登录。";
export const ADMIN_FORBIDDEN_MESSAGE = "当前管理员账号无权访问此内容。";
export const ADMIN_API_PREFIX = "/admin/api";

export type AdminErrorKind = "AUTH_REQUIRED" | "FORBIDDEN" | "UNAVAILABLE" | "REQUEST_ERROR";
export type AdminErrorPresentation = { kind: AdminErrorKind; message: string };
type StatusError = Error & { status?: number };

export function adminResponseError(response: Response, fallback: string): StatusError {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  const accessLogin = response.status === 401 || response.redirected
    || (response.ok && contentType.includes("text/html"));
  if (accessLogin) reportAdminAuthOutcome("ANONYMOUS");
  else if (response.status === 403) reportAdminAuthOutcome("FORBIDDEN");
  const error = new Error(accessLogin ? ADMIN_AUTH_REQUIRED_MESSAGE
    : response.status === 403 ? ADMIN_FORBIDDEN_MESSAGE : fallback) as StatusError;
  error.status = accessLogin ? 401 : response.status;
  return error;
}

export function adminErrorPresentation(reason: unknown, unavailableMessage: string): AdminErrorPresentation {
  const status = reason instanceof Error ? (reason as StatusError).status : undefined;
  if (status === 401) return { kind: "AUTH_REQUIRED", message: ADMIN_AUTH_REQUIRED_MESSAGE };
  if (status === 403) return { kind: "FORBIDDEN", message: ADMIN_FORBIDDEN_MESSAGE };
  if (status && status >= 500) return { kind: "UNAVAILABLE", message: unavailableMessage };
  if (status === undefined) return { kind: "UNAVAILABLE", message: unavailableMessage };
  return { kind: "REQUEST_ERROR", message: reason instanceof Error && reason.message ? reason.message : unavailableMessage };
}
