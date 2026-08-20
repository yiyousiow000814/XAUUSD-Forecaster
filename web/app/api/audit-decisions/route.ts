import { readAuditDetailSnapshot, writeAuditDetailSnapshot } from "../_shared/audit-detail-snapshot";
import { AUDIT_SNAPSHOT_IDS } from "../_shared/dashboard-snapshot";

export const dynamic = "force-dynamic";
const fields = ["generated_at", "recent_decisions"];

export async function GET() {
  return readAuditDetailSnapshot(AUDIT_SNAPSHOT_IDS.decisions, fields, "等待决策审计详情首次同步");
}

export async function POST(request: Request) {
  return writeAuditDetailSnapshot(
    request, AUDIT_SNAPSHOT_IDS.decisions,
    "invalid audit decisions payload", "audit-decisions-write",
  );
}
