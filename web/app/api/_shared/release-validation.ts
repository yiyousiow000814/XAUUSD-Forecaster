const validationHeader = "x-aurum-release-validation";
const validationRunHeader = "x-aurum-validation-run";
const requestIdHeader = "x-aurum-request-id";

export function releaseValidationDryRun(
  request: Request,
  routeFamily: string,
) {
  if (request.headers.get(validationHeader) !== "dry-run") return null;
  const validationRun = request.headers.get(validationRunHeader)?.trim();
  const requestId = request.headers.get(requestIdHeader)?.trim();
  if (!validationRun || !requestId) {
    return Response.json(
      { error: "release validation identity required" },
      { status: 400 },
    );
  }
  return Response.json({
    status: "DRY_RUN_OK",
    route_family: routeFamily,
    validation_run: validationRun,
    request_id: requestId,
    mutated: false,
  }, { headers: { "Cache-Control": "no-store" } });
}
