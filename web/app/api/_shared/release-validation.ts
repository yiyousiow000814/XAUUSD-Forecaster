const validationHeader = "x-aurum-release-validation";
const validationRunHeader = "x-aurum-validation-run";
const requestIdHeader = "x-aurum-request-id";

export type ReleaseValidationContext = {
  routeFamily: string;
  validationRun: string;
  requestId: string;
};

export async function authorizeReleaseValidation(
  request: Request,
  routeFamily: string,
  authorize: (request: Request) => Promise<boolean>,
): Promise<ReleaseValidationContext | Response | null> {
  if (!await authorize(request)) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }
  return beginReleaseValidation(request, routeFamily);
}

/** Parse release-validation identity after the route's normal authentication. */
export function beginReleaseValidation(
  request: Request,
  routeFamily: string,
): ReleaseValidationContext | Response | null {
  if (request.headers.get(validationHeader) !== "dry-run") return null;
  const validationRun = request.headers.get(validationRunHeader)?.trim();
  const requestId = request.headers.get(requestIdHeader)?.trim();
  if (!validationRun || !requestId) {
    return Response.json(
      { error: "release validation identity required" },
      { status: 400 },
    );
  }
  return { routeFamily, validationRun, requestId };
}

export function releaseValidationResponse(
  context: ReleaseValidationContext,
  work: Record<string, unknown>,
) {
  return Response.json({
    status: "DRY_RUN_OK",
    route_family: context.routeFamily,
    validation_run: context.validationRun,
    request_id: context.requestId,
    mutated: false,
    work,
  }, { headers: { "Cache-Control": "no-store" } });
}

/** Exercise D1 JSON1 without writing an authoritative or validation row. */
export async function validateJsonWithD1(
  binding: D1Database,
  serialized: string,
): Promise<boolean> {
  const row = await binding.prepare(
    "SELECT json_valid(?) AS valid",
  ).bind(serialized).first<{ valid: number }>();
  return Number(row?.valid ?? 0) === 1;
}

/** Validate exact UTF-8 request bytes in D1 without a Worker decode/re-encode pass. */
export async function validateJsonBytesWithD1(
  binding: D1Database,
  bytes: ArrayBuffer,
): Promise<boolean> {
  const row = await binding.prepare(
    "SELECT json_valid(CAST(? AS TEXT)) AS valid",
  ).bind(bytes).first<{ valid: number }>();
  return Number(row?.valid ?? 0) === 1;
}

export function isReleaseValidationContext(
  value: ReleaseValidationContext | Response | null,
): value is ReleaseValidationContext {
  return value !== null && !(value instanceof Response);
}
