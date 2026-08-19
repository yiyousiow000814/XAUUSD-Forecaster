import { env } from "cloudflare:workers";
import {
  authenticateDashboardOperatorRequest,
  dashboardOperatorSessionResponse,
} from "../../../api/_shared/dashboard-operator-auth";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const authorization = await authenticateDashboardOperatorRequest(request, env);
  return dashboardOperatorSessionResponse(authorization);
}
