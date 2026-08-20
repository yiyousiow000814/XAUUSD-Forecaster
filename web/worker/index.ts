import { routeApiRequest, type ApiRouteResult } from "./api-router";

declare const __AURUM_DEPLOYMENT__: {
  branch: string;
  commit_sha: string;
  is_preview: boolean;
};

const isPreview = __AURUM_DEPLOYMENT__.is_preview;

const LEGACY_REDIRECTS: Record<string, string> = {
  "/status": "/admin/ai-usage",
  "/assistant": "/admin/assistant",
  "/retry-jobs": "/admin/retry-jobs",
};

const safeRequestId = (request: Request) => {
  const supplied = request.headers.get("x-correlation-id")?.trim();
  if (supplied && /^[A-Za-z0-9._:-]{1,128}$/.test(supplied)) return supplied;
  return request.headers.get("cf-ray") ?? crypto.randomUUID();
};

const declaredBytes = (request: Request) => {
  const raw = request.headers.get("content-length");
  if (raw === null) return request.body ? null : 0;
  const value = Number(raw);
  if (Number.isSafeInteger(value) && value >= 0) return value;
  return null;
};

function diagnosticResponse(
  response: Response,
  metadata: {
    requestId: string;
    versionId: string;
    route: string;
    result: ApiRouteResult;
    wallDurationMs: number;
  },
) {
  const headers = new Headers(response.headers);
  headers.set("X-Aurum-Request-Id", metadata.requestId);
  headers.set("X-Aurum-Git-SHA", __AURUM_DEPLOYMENT__.commit_sha || "unknown");
  headers.set("X-Aurum-Worker-Version", metadata.versionId);
  headers.set("X-Aurum-Route", metadata.route);
  headers.set("X-Aurum-Resource", metadata.result.resource);
  headers.set("X-Aurum-D1-Operations", String(metadata.result.d1Operations ?? "unknown"));
  headers.set("X-Aurum-Request-Bytes", String(metadata.result.requestBytes ?? "unknown"));
  headers.set("X-Aurum-Response-Bytes", String(metadata.result.responseBytes ?? "unknown"));
  headers.set("Server-Timing", `aurum;dur=${metadata.wallDurationMs.toFixed(2)}`);
  if (metadata.result.failureStage) {
    headers.set("X-Aurum-Failure-Stage", metadata.result.failureStage);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const started = performance.now();
    const url = new URL(request.url);
    const requestId = safeRequestId(request);
    const versionId = env.CF_VERSION_METADATA?.id ?? "local";
    let result: ApiRouteResult;
    try {
      const legacyRedirect = LEGACY_REDIRECTS[url.pathname] ?? null;
      if (legacyRedirect) {
        result = {
          response: Response.redirect(new URL(legacyRedirect, request.url), 307),
          resource: "legacy-redirect", d1Operations: 0,
          requestBytes: 0, responseBytes: 0, failureStage: null,
        };
      } else if (url.pathname === "/_vinext/image") {
        const {
          handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES,
        } = await import("vinext/server/image-optimization");
        const response = await handleImageOptimization(request, {
          fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
          transformImage: async (body, { width, format, quality }) => {
            if (!["image/jpeg", "image/webp", "image/avif"].includes(format)) {
              throw new Error("Unsupported negotiated image output format");
            }
            const transformed = await env.IMAGES.input(body)
              .transform(width > 0 ? { width } : {})
              .output({
                format: format as "image/jpeg" | "image/webp" | "image/avif",
                quality,
              });
            return transformed.response();
          },
        }, [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES]);
        result = {
          response, resource: "image", d1Operations: 0,
          requestBytes: 0, responseBytes: null,
          failureStage: response.status >= 500 ? "image_transform" : null,
        };
      } else if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/admin/api/")) {
        const routed = await routeApiRequest(request, env, isPreview);
        if (routed) result = routed;
        else {
          const { default: handler } = await import("vinext/server/app-router-entry");
          const response = await handler.fetch(request, env, ctx);
          result = {
            response, resource: "unmatched-api", d1Operations: null,
            requestBytes: declaredBytes(request), responseBytes: null,
            failureStage: response.status >= 500 ? "framework_fallback" : null,
          };
        }
      } else {
        const { default: handler } = await import("vinext/server/app-router-entry");
        const response = await handler.fetch(request, env, ctx);
        result = {
          response, resource: "ssr-fallback", d1Operations: null,
          requestBytes: declaredBytes(request), responseBytes: null,
          failureStage: response.status >= 500 ? "ssr" : null,
        };
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Worker request failed";
      const serialized = JSON.stringify({ error: "request failed", request_id: requestId });
      result = {
        response: new Response(serialized, {
          status: 503,
          headers: { "Content-Type": "application/json; charset=utf-8" },
        }),
        resource: url.pathname.split("/").filter(Boolean).at(-1) ?? "root",
        d1Operations: null,
        requestBytes: declaredBytes(request),
        responseBytes: new TextEncoder().encode(serialized).byteLength,
        failureStage: "exception",
      };
      console.error({
        event: "AURUM_WORKER_EXCEPTION", request_id: requestId,
        route: url.pathname, message,
      });
    }

    result.requestBytes ??= declaredBytes(request);
    const wallDurationMs = performance.now() - started;
    console.log({
      event: "AURUM_WORKER_INVOCATION",
      git_commit_sha: __AURUM_DEPLOYMENT__.commit_sha || null,
      worker_version: versionId,
      route: url.pathname,
      resource: result.resource,
      request_id: requestId,
      wall_duration_ms: Number(wallDurationMs.toFixed(3)),
      d1_operation_count: result.d1Operations,
      request_byte_count: result.requestBytes,
      response_byte_count: result.responseBytes,
      failure_stage: result.failureStage,
      status: result.response.status,
    });
    return diagnosticResponse(result.response, {
      requestId, versionId, route: url.pathname, result, wallDurationMs,
    });
  },
} satisfies ExportedHandler<Env>;

export default worker;
