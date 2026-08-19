export const ADMIN_RELOGIN_MESSAGE = "管理员会话已过期，请重新登录。";
export const ADMIN_FORBIDDEN_MESSAGE = "当前管理员账号无权访问此内容。";

export type AdminErrorKind = "AUTH_REQUIRED" | "FORBIDDEN" | "UNAVAILABLE" | "REQUEST_ERROR";
export type AdminErrorPresentation = { kind: AdminErrorKind; message: string };
type StatusError = Error & { status?: number };

export function adminResponseError(response: Response, fallback: string): StatusError {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  const accessLogin = response.status === 401 || response.redirected
    || (response.ok && contentType.includes("text/html"));
  const error = new Error(accessLogin ? ADMIN_RELOGIN_MESSAGE
    : response.status === 403 ? ADMIN_FORBIDDEN_MESSAGE : fallback) as StatusError;
  error.status = accessLogin ? 401 : response.status;
  return error;
}

export function adminErrorPresentation(reason: unknown, unavailableMessage: string): AdminErrorPresentation {
  const status = reason instanceof Error ? (reason as StatusError).status : undefined;
  if (status === 401) return { kind: "AUTH_REQUIRED", message: ADMIN_RELOGIN_MESSAGE };
  if (status === 403) return { kind: "FORBIDDEN", message: ADMIN_FORBIDDEN_MESSAGE };
  if (status && status >= 500) return { kind: "UNAVAILABLE", message: unavailableMessage };
  if (status === undefined) return { kind: "UNAVAILABLE", message: unavailableMessage };
  return { kind: "REQUEST_ERROR", message: reason instanceof Error && reason.message ? reason.message : unavailableMessage };
}
