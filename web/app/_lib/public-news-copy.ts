const internalUuid = /(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])/giu;
const internalReasonField = /\b(?:matched_candidate_id|candidate_id|annotation_id)\b/giu;
const labeledInternalId = /(?:候选|已有报道记录|已有报道)\s*[：:#]?\s*[0-9a-f]{8}[0-9a-f-]{0,40}/giu;
const internalUuidProbe = /(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])/iu;

const needsPublicImpactRewrite = (value: string) => value.includes("候选")
  || value.includes("已有报道")
  || value.includes("candidate_id")
  || value.includes("annotation_id")
  || internalUuidProbe.test(value);

export const publicImpactReason = (value: unknown) => {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!needsPublicImpactRewrite(trimmed)) return trimmed;
  return trimmed
    .replace(labeledInternalId, "系统中已有的一篇报道")
    .replace(internalUuid, "系统中已有的一篇报道")
    .replace(internalReasonField, "系统中已有的一篇报道")
    .replaceAll("候选", "已有报道");
};

export const publicNewsRecord = (value: unknown): unknown => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const detail = value as Record<string, unknown>;
  if (detail.payload && typeof detail.payload === "object" && !Array.isArray(detail.payload)) {
    const payload = publicNewsRecord(detail.payload);
    return payload === detail.payload ? detail : { ...detail, payload };
  }
  if (!("impact_reason_zh" in detail)) return detail;
  const impactReason = publicImpactReason(detail.impact_reason_zh);
  return impactReason === detail.impact_reason_zh
    ? detail : { ...detail, impact_reason_zh: impactReason };
};
