import { readAuditDetailSnapshot, writeAuditDetailSnapshot } from "../_shared/audit-detail-snapshot";
import { AUDIT_SNAPSHOT_IDS } from "../_shared/dashboard-snapshot";

export const dynamic = "force-dynamic";
const fields = [
  "generated_at", "storylines", "market_narrative_candidates",
  "archived_storylines", "archived_story_event_candidates",
  "story_event_candidates", "market_reaction_streams", "theme_streams",
  "unassigned_story_events", "storyline_summary",
];

export async function GET() {
  return readAuditDetailSnapshot(AUDIT_SNAPSHOT_IDS.stories, fields, "等待事件脉络详情首次同步");
}

export async function POST(request: Request) {
  return writeAuditDetailSnapshot(request, AUDIT_SNAPSHOT_IDS.stories, "invalid audit stories payload");
}
