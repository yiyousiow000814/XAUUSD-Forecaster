/// <reference types="vite/client" />

import {
  AUDIT_DETAIL_SNAPSHOT_BYTES,
  AUDIT_SNAPSHOT_IDS,
  AUDIT_SUMMARY_SNAPSHOT_BYTES,
  MAX_DASHBOARD_SNAPSHOT_BYTES,
  publicStatusJsonExpression,
  readBoundedBody,
  writeDashboardStatusSnapshots,
  writeSerializedDashboardSnapshot,
} from "../app/api/_shared/dashboard-snapshot";
import {
  d1CapabilityFailure,
  D1CapabilityError,
  requireD1Capabilities,
} from "../app/api/_shared/d1-capabilities";
import {
  beginReleaseValidation,
  isReleaseValidationContext,
  releaseValidationResponse,
  validateJsonWithD1,
} from "../app/api/_shared/release-validation";

declare const __AURUM_DEPLOYMENT__: {
  branch: string;
  commit_sha: string;
  is_preview: boolean;
};

type RouteModule = Record<string, ((request: Request) => Response | Promise<Response>) | undefined>;
type RouteLoader = () => Promise<RouteModule>;

const routeModules = import.meta.glob<RouteModule>([
  "../app/api/**/route.ts",
  "../app/admin/api/**/route.ts",
]);

type SnapshotRoute = {
  id: number; invalid: string; maxBytes: number;
  legacyFields?: Record<string, number | null>;
  readSql?: string;
};

const SNAPSHOT_ROUTES: Record<string, SnapshotRoute> = {
  "/api/audit": { id: AUDIT_SNAPSHOT_IDS.summary, invalid: "invalid audit payload", maxBytes: AUDIT_SUMMARY_SNAPSHOT_BYTES, legacyFields: {
    generated_at: null, news_metrics: null, daily_news_brief_summary: null,
    storyline_summary: null, news_evidence_summary: null, news_feature_policy: null,
  } },
  "/api/audit-decisions": { id: AUDIT_SNAPSHOT_IDS.decisions, invalid: "invalid audit decisions payload", maxBytes: AUDIT_DETAIL_SNAPSHOT_BYTES, legacyFields: { generated_at: null, recent_decisions: 20 } },
  "/api/audit-briefs": { id: AUDIT_SNAPSHOT_IDS.briefs, invalid: "invalid audit briefs payload", maxBytes: AUDIT_DETAIL_SNAPSHOT_BYTES, legacyFields: { generated_at: null, daily_news_briefs: 3 } },
  "/api/audit-stories": { id: AUDIT_SNAPSHOT_IDS.stories, invalid: "invalid audit stories payload", maxBytes: AUDIT_DETAIL_SNAPSHOT_BYTES, legacyFields: {
    generated_at: null, storylines: 20, market_narrative_candidates: 50,
    archived_storylines: 20, archived_story_event_candidates: 50,
    story_event_candidates: 50, market_reaction_streams: 12,
    theme_streams: 12, unassigned_story_events: 50, storyline_summary: null,
  } },
  "/api/learning": { id: 3, invalid: "invalid learning payload", maxBytes: MAX_DASHBOARD_SNAPSHOT_BYTES },
  "/api/market-chart": { id: 2, invalid: "invalid market chart payload", maxBytes: MAX_DASHBOARD_SNAPSHOT_BYTES },
};

function legacyAuditProjection(fields: Record<string, number | null>) {
  const arrayValue = (field: string) => {
    if (field === "recent_decisions") {
      return `json_set(json_remove(item.item_value, '$.features'), '$.predictions', json(coalesce(`
        + `(SELECT json_group_array(json(prediction_value)) FROM (`
        + `SELECT prediction.value AS prediction_value FROM json_each(item.item_value, '$.predictions') prediction LIMIT 8)), '[]')))`;
    }
    if (field === "daily_news_briefs") return `json_remove(item.item_value, '$.brief_json')`;
    return "json(item.item_value)";
  };
  return `json_object(${Object.entries(fields).flatMap(([field, limit]) => [
    `'${field}'`, limit === null
      ? `json_extract(payload, '$.${field}')`
      : `json(coalesce((SELECT json_group_array(${arrayValue(field)}) FROM (`
        + `SELECT value AS item_value FROM json_each(payload, '$.${field}') LIMIT ${limit}) item), '[]'))`,
  ]).join(", ")})`;
}

function auditSnapshotSql(route: SnapshotRoute) {
  if (!route.legacyFields) return null;
  return `WITH candidates(payload, received_at, preference) AS (
    SELECT payload, received_at, 0 FROM dashboard_snapshots
     WHERE id=${route.id} AND json_valid(payload)
    UNION ALL
    SELECT ${legacyAuditProjection(route.legacyFields)}, received_at, 1
      FROM dashboard_snapshots WHERE id=4 AND json_valid(payload)
  ), selected(payload) AS (
    SELECT payload FROM candidates
     WHERE length(CAST(payload AS BLOB)) <= ${route.maxBytes}
     ORDER BY julianday(received_at) DESC, preference ASC LIMIT 1
  )
  SELECT payload, length(CAST(payload AS BLOB)) AS payload_bytes FROM selected`;
}

for (const route of Object.values(SNAPSHOT_ROUTES)) {
  if (route.legacyFields) route.readSql = auditSnapshotSql(route) ?? undefined;
}

const PUBLIC_STATUS_SQL = `WITH selected(payload) AS (
  SELECT payload FROM dashboard_snapshots
   WHERE id IN (5, 1) AND json_valid(payload)
   ORDER BY julianday(received_at) DESC,
            CASE id WHEN 5 THEN 0 ELSE 1 END
   LIMIT 1
), public(payload) AS (
  SELECT ${publicStatusJsonExpression()} FROM selected
), measured AS (
  SELECT payload,
    CASE
      WHEN json_type(payload, '$.system.quote_age_seconds') IN ('integer', 'real')
       AND julianday(json_extract(payload, '$.generated_at')) IS NOT NULL
      THEN max(0.0,
        CAST(json_extract(payload, '$.system.quote_age_seconds') AS REAL)
        + max(0.0, (julianday('now')
          - julianday(json_extract(payload, '$.generated_at'))) * 86400.0)
      )
      ELSE NULL
    END AS quote_age_seconds
  FROM public
), finalized(payload) AS (
  SELECT json_set(
    payload,
    '$.observation_scope', 'D1_SNAPSHOT',
    '$.system.quote_age_seconds', quote_age_seconds,
    '$.system.online', json(CASE
      WHEN json_extract(payload, '$.system.online') = 1
       AND quote_age_seconds IS NOT NULL AND quote_age_seconds <= 75
      THEN 'true' ELSE 'false' END)
  ) FROM measured
)
SELECT payload, length(CAST(payload AS BLOB)) AS payload_bytes FROM finalized`;

export type ApiRouteResult = {
  response: Response;
  resource: string;
  d1Operations: number | null;
  requestBytes: number | null;
  responseBytes: number | null;
  failureStage: string | null;
};

const bytes = (value: string) => new TextEncoder().encode(value).byteLength;

async function isAuthorized(request: Request, expected: string | undefined) {
  const provided = /^Bearer\s+(.+)$/i.exec(
    request.headers.get("authorization") ?? "",
  )?.[1];
  if (!expected || !provided) return false;
  const digest = async (value: string) => new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
  );
  const [expectedDigest, providedDigest] = await Promise.all([
    digest(expected), digest(provided),
  ]);
  let difference = expectedDigest.length ^ providedDigest.length;
  for (let index = 0; index < expectedDigest.length; index += 1) {
    difference |= expectedDigest[index] ^ providedDigest[index];
  }
  return difference === 0;
}

function json(payload: unknown, status = 200, headers: HeadersInit = {}) {
  const serialized = JSON.stringify(payload);
  return {
    response: new Response(serialized, {
      status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        ...headers,
      },
    }),
    responseBytes: bytes(serialized),
  };
}

function result(
  response: Response,
  resource: string,
  options: Partial<Omit<ApiRouteResult, "response" | "resource">> = {},
): ApiRouteResult {
  return {
    response,
    resource,
    d1Operations: options.d1Operations ?? null,
    requestBytes: options.requestBytes ?? null,
    responseBytes: options.responseBytes ?? null,
    failureStage: options.failureStage ?? null,
  };
}

async function snapshotRead(
  binding: D1Database | undefined,
  route: SnapshotRoute,
  resource: string,
) {
  if (!binding) {
    const response = json({ error: `等待${resource}首次同步` }, 503);
    return result(response.response, resource, {
      d1Operations: 0, responseBytes: response.responseBytes,
      failureStage: "d1_binding",
    });
  }
  try {
    const statement = route.readSql
      ? binding.prepare(route.readSql)
      : binding.prepare(
        `SELECT payload, length(CAST(payload AS BLOB)) AS payload_bytes
         FROM dashboard_snapshots WHERE id = ? AND json_valid(payload)`,
      ).bind(route.id);
    const row = await statement.first<{ payload: string; payload_bytes: number }>();
    if (row) {
      return result(new Response(row.payload, {
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "private, max-age=15",
        },
      }), resource, {
        d1Operations: 1, responseBytes: Number(row.payload_bytes),
      });
    }
  } catch {
    // The resource owns its D1 failure and reports it below.
  }
  const response = json({ error: `等待${resource}首次同步` }, 503);
  return result(response.response, resource, {
    d1Operations: 1, responseBytes: response.responseBytes,
    failureStage: "d1_read",
  });
}

async function publicStatusRead(binding: D1Database | undefined) {
  if (!binding) {
    const response = json({ error: "等待公开状态快照" }, 503);
    return result(response.response, "status", {
      d1Operations: 0, responseBytes: response.responseBytes,
      failureStage: "d1_binding",
    });
  }
  try {
    const row = await binding.prepare(PUBLIC_STATUS_SQL).first<{
      payload: string; payload_bytes: number;
    }>();
    if (row?.payload) {
      return result(new Response(row.payload, {
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store, max-age=0",
        },
      }), "status", {
        d1Operations: 1, responseBytes: Number(row.payload_bytes),
      });
    }
  } catch {
    // The public status read remains fail-closed.
  }
  const response = json({ error: "等待公开状态快照" }, 503);
  return result(response.response, "status", {
    d1Operations: 1, responseBytes: response.responseBytes,
    failureStage: "d1_read",
  });
}

async function snapshotWrite(
  request: Request,
  env: Env,
  resource: string,
  id: number | null,
  invalid: string,
  routeFamily: string,
  maxBytes = MAX_DASHBOARD_SNAPSHOT_BYTES,
) {
  if (!await isAuthorized(request, env.INGEST_TOKEN)) {
    const response = json({ error: "unauthorized" }, 401);
    return result(response.response, resource, {
      d1Operations: 0, responseBytes: response.responseBytes,
      failureStage: "authorization",
    });
  }
  if (!env.DB) {
    const response = json({ error: "database unavailable" }, 503);
    return result(response.response, resource, {
      d1Operations: 0, responseBytes: response.responseBytes,
      failureStage: "d1_binding",
    });
  }
  const validation = beginReleaseValidation(request, routeFamily);
  if (validation instanceof Response) {
    return result(validation, resource, {
      d1Operations: 0,
      failureStage: "release_validation_identity",
    });
  }
  const body = await readBoundedBody(request, maxBytes);
  if (body.status === "too_large") {
    const response = json({ error: "payload too large" }, 413);
    return result(response.response, resource, {
      d1Operations: 0, responseBytes: response.responseBytes,
      failureStage: "request_bound",
    });
  }
  const writeResult = isReleaseValidationContext(validation)
    ? await validateJsonWithD1(env.DB, body.serialized) ? "validated" : "invalid"
    : id === null
      ? await writeDashboardStatusSnapshots(body.serialized, env.DB)
      : await writeSerializedDashboardSnapshot(body.serialized, env.DB, id);
  if (writeResult === "invalid") {
    const response = json({ error: invalid }, 400);
    return result(response.response, resource, {
      d1Operations: 1, requestBytes: body.receivedBytes,
      responseBytes: response.responseBytes, failureStage: "json_validation",
    });
  }
  if (writeResult === "validated" && isReleaseValidationContext(validation)) {
    const response = releaseValidationResponse(validation, {
      body: "bounded-read", json: "d1-json1",
      mutation_boundary: id === null ? "status-snapshot-upsert" : "snapshot-upsert",
    });
    const serialized = await response.clone().text();
    return result(response, resource, {
      d1Operations: 1,
      requestBytes: body.receivedBytes,
      responseBytes: bytes(serialized),
    });
  }
  const payload = resource === "status" ? {
    status: "OK",
    main_revision:
      __AURUM_DEPLOYMENT__.branch === "main"
      && /^[0-9a-f]{40}$/.test(__AURUM_DEPLOYMENT__.commit_sha)
        ? __AURUM_DEPLOYMENT__.commit_sha : null,
  } : { status: "OK" };
  const response = json(payload);
  return result(response.response, resource, {
    d1Operations: 1, requestBytes: body.receivedBytes,
    responseBytes: response.responseBytes,
  });
}

async function ingestHealth(env: Env) {
  if (!env.DB) {
    const response = json({
      status: "ERROR", error: "database unavailable",
      error_code: "D1_BINDING_MISSING",
    }, 503, { "Cache-Control": "no-store" });
    return result(response.response, "status", {
      d1Operations: 0, responseBytes: response.responseBytes,
      failureStage: "d1_binding",
    });
  }
  try {
    await requireD1Capabilities(env.DB, [
      "operator_retry_scheduling", "paged_news_evidence",
    ]);
    const response = json({
      status: "OK",
      main_revision:
        __AURUM_DEPLOYMENT__.branch === "main"
        && /^[0-9a-f]{40}$/.test(__AURUM_DEPLOYMENT__.commit_sha)
          ? __AURUM_DEPLOYMENT__.commit_sha : null,
    }, 200, { "Cache-Control": "no-store" });
    return result(response.response, "ingest-health", {
      d1Operations: 1, responseBytes: response.responseBytes,
    });
  } catch (error) {
    const payload = error instanceof D1CapabilityError
      ? d1CapabilityFailure(error)
      : { status: "ERROR", error: "database unavailable" };
    const response = json(payload, 503, { "Cache-Control": "no-store" });
    return result(response.response, "ingest-health", {
      d1Operations: 1, responseBytes: response.responseBytes,
      failureStage: "d1_capability",
    });
  }
}

function moduleKey(pathname: string) {
  return pathname.startsWith("/admin/api/")
    ? `../app${pathname}/route.ts`
    : `../app${pathname}/route.ts`;
}

async function genericRoute(request: Request, pathname: string) {
  const loader = routeModules[moduleKey(pathname)] as RouteLoader | undefined;
  if (!loader) return null;
  const routeModule = await loader();
  const handler = routeModule[request.method] ?? (
    request.method === "HEAD" ? routeModule.GET : undefined
  );
  if (!handler) {
    const response = json({ error: "method not allowed" }, 405, { Allow: "GET, POST" });
    return result(response.response, pathname.split("/").at(-1) ?? "api", {
      d1Operations: 0, responseBytes: response.responseBytes,
      failureStage: "method",
    });
  }
  const response = await handler(request);
  const rawContentLength = response.headers.get("content-length");
  const contentLength = rawContentLength === null ? null : Number(rawContentLength);
  return result(response, pathname.split("/").at(-1) ?? "api", {
    responseBytes: contentLength !== null
      && Number.isSafeInteger(contentLength) && contentLength >= 0
      ? contentLength : null,
    failureStage: response.status >= 500 ? "route_handler" : null,
  });
}

export async function routeApiRequest(
  request: Request,
  env: Env,
  isPreview: boolean,
): Promise<ApiRouteResult | null> {
  const pathname = new URL(request.url).pathname;
  if (isPreview) return genericRoute(request, pathname);

  if (request.method === "GET" && pathname === "/api/status") {
    return publicStatusRead(env.DB);
  }
  if (pathname === "/api/ingest") {
    if (request.method === "GET") return ingestHealth(env);
    if (request.method === "POST") {
      return snapshotWrite(
        request, env, "status", null, "invalid status payload", "status-ingest",
      );
    }
  }
  const snapshot = SNAPSHOT_ROUTES[pathname];
  if (snapshot) {
    if (request.method === "GET") {
      return snapshotRead(env.DB, snapshot, pathname.slice(5));
    }
    if (request.method === "POST") {
      return snapshotWrite(
        request, env, pathname.slice(5), snapshot.id, snapshot.invalid,
        `${pathname.slice(5)}-write`,
        snapshot.maxBytes,
      );
    }
  }
  return genericRoute(request, pathname);
}
