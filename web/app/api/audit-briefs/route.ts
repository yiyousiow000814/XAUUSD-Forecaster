import { readAuditDetailSnapshot, writeAuditDetailSnapshot } from "../_shared/audit-detail-snapshot";
import { AUDIT_SNAPSHOT_IDS } from "../_shared/dashboard-snapshot";

export const dynamic = "force-dynamic";
const fields = ["generated_at", "daily_news_briefs"];

export async function GET() {
  return readAuditDetailSnapshot(AUDIT_SNAPSHOT_IDS.briefs, fields, "等待每日简报详情首次同步");
}

export async function POST(request: Request) {
  return writeAuditDetailSnapshot(request, AUDIT_SNAPSHOT_IDS.briefs, "invalid audit briefs payload");
}
