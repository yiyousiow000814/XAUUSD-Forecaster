import { canonicalJson } from "./assistant-content";

export const ASSISTANT_EVIDENCE_PROTOCOL = "assistant.evidence.v1";
export const ASSISTANT_EVIDENCE_VALIDATOR_VERSION = "assistant-evidence-validator-v1";

export const ASSISTANT_EVIDENCE_LIMITS = {
  maxClaims: 12,
  maxClaimCharacters: 4_000,
  maxEvidencePerClaim: 8,
  maxCitedEvidence: 100,
} as const;

export type AssistantEvidenceMode =
  | "CITATION_COVERAGE"
  | "NO_CITABLE_EVIDENCE"
  | "INSUFFICIENT_EVIDENCE";

export type AssistantEvidenceReceipt = {
  protocol: typeof ASSISTANT_EVIDENCE_PROTOCOL;
  validator_version: typeof ASSISTANT_EVIDENCE_VALIDATOR_VERSION;
  mode: AssistantEvidenceMode;
  claim_count: number;
  citation_count: number;
  available_evidence_ids: string[];
  cited_evidence_ids: string[];
  claims: Array<{
    claim_id: string;
    line_index: number;
    text_sha256: string;
    evidence_ids: string[];
  }>;
  coverage_complete: boolean;
  entailment_status: "NOT_VERIFIED";
  answer_sha256: string;
  receipt_sha256: string;
};

export class AssistantEvidenceValidationError extends Error {}

const evidenceId = /^[A-Za-z0-9:._-]{1,128}$/;

const digest = async (value: string) => {
  const output = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(output), byte => byte.toString(16).padStart(2, "0")).join("");
};

const orderedEvidenceIds = (value: unknown, maximum: number) => {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new AssistantEvidenceValidationError("Evidence IDs are invalid");
  }
  const result: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (typeof item !== "string" || !evidenceId.test(item) || seen.has(item)) {
      throw new AssistantEvidenceValidationError("Evidence ID is invalid or duplicated");
    }
    seen.add(item);
    result.push(item);
  }
  return result;
};

const claimText = (value: unknown) => {
  if (typeof value !== "string") {
    throw new AssistantEvidenceValidationError("Evidence claim text is invalid");
  }
  let normalized = value.normalize("NFC").trim();
  if (Array.from(normalized).some(character => {
    const codepoint = character.codePointAt(0) ?? 0;
    return codepoint < 32 || codepoint === 127;
  })) throw new AssistantEvidenceValidationError("Evidence claim must be one safe line");
  normalized = normalized.replace(/\s+/gu, " ");
  if (
    !normalized
    || Array.from(normalized).length > ASSISTANT_EVIDENCE_LIMITS.maxClaimCharacters
  ) throw new AssistantEvidenceValidationError("Evidence claim text is out of bounds");
  return normalized;
};

export async function buildAssistantEvidenceValidation(
  value: unknown,
  availableEvidenceIds: unknown,
  options: { mode?: AssistantEvidenceMode; maxCitedEvidence?: number } = {},
) {
  const maximum = options.maxCitedEvidence ?? 20;
  if (!Number.isSafeInteger(maximum) || maximum < 1
    || maximum > ASSISTANT_EVIDENCE_LIMITS.maxCitedEvidence) {
    throw new Error("Evidence citation bound is invalid");
  }
  const available = orderedEvidenceIds(
    availableEvidenceIds, ASSISTANT_EVIDENCE_LIMITS.maxCitedEvidence,
  );
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).length !== 1 || !Object.hasOwn(value, "claims")) {
    throw new AssistantEvidenceValidationError("Evidence answer envelope is invalid");
  }
  const rawClaims = (value as Record<string, unknown>).claims;
  if (!Array.isArray(rawClaims) || rawClaims.length < 1
    || rawClaims.length > ASSISTANT_EVIDENCE_LIMITS.maxClaims) {
    throw new AssistantEvidenceValidationError("Evidence claim count is invalid");
  }
  const mode = options.mode ?? (available.length ? "CITATION_COVERAGE" : "NO_CITABLE_EVIDENCE");
  if (mode === "CITATION_COVERAGE" && !available.length) {
    throw new AssistantEvidenceValidationError("Citation coverage requires evidence");
  }
  if (mode !== "CITATION_COVERAGE" && available.length) {
    throw new AssistantEvidenceValidationError("Uncited mode cannot hide available evidence");
  }
  const allowed = new Set(available);
  const cited: string[] = [];
  const citedSet = new Set<string>();
  const texts: string[] = [];
  const claims: AssistantEvidenceReceipt["claims"] = [];
  let citationCount = 0;
  for (const [index, rawClaim] of rawClaims.entries()) {
    if (!rawClaim || typeof rawClaim !== "object" || Array.isArray(rawClaim)
      || Object.keys(rawClaim).sort().join("|") !== "evidence_ids|text") {
      throw new AssistantEvidenceValidationError("Evidence claim fields are invalid");
    }
    const claim = rawClaim as Record<string, unknown>;
    const text = claimText(claim.text);
    const refs = orderedEvidenceIds(
      claim.evidence_ids, ASSISTANT_EVIDENCE_LIMITS.maxEvidencePerClaim,
    );
    if (mode === "CITATION_COVERAGE") {
      if (!refs.length || refs.some(item => !allowed.has(item))) {
        throw new AssistantEvidenceValidationError(
          "Every evidence-backed claim needs retrieved citations",
        );
      }
    } else if (refs.length) {
      throw new AssistantEvidenceValidationError("Uncited answer mode contains citations");
    }
    for (const item of refs) {
      if (!citedSet.has(item)) {
        citedSet.add(item);
        cited.push(item);
      }
    }
    if (cited.length > maximum) {
      throw new AssistantEvidenceValidationError("Cited evidence exceeds its bound");
    }
    citationCount += refs.length;
    texts.push(text);
    claims.push({
      claim_id: `claim-${index + 1}`,
      line_index: index,
      text_sha256: await digest(text),
      evidence_ids: refs,
    });
  }
  const answer = texts.join("\n");
  const receiptWithoutHash = {
    protocol: ASSISTANT_EVIDENCE_PROTOCOL,
    validator_version: ASSISTANT_EVIDENCE_VALIDATOR_VERSION,
    mode,
    claim_count: claims.length,
    citation_count: citationCount,
    available_evidence_ids: available,
    cited_evidence_ids: cited,
    claims,
    coverage_complete: mode === "CITATION_COVERAGE",
    entailment_status: "NOT_VERIFIED" as const,
    answer_sha256: await digest(answer),
  };
  const receipt: AssistantEvidenceReceipt = {
    ...receiptWithoutHash,
    receipt_sha256: await digest(canonicalJson(receiptWithoutHash)),
  };
  return { answer, evidenceIds: cited, receipt };
}

export async function parseAssistantEvidenceReceipt(
  value: unknown,
  input: {
    answer: string;
    availableEvidenceIds: string[];
    mode: AssistantEvidenceMode;
    maxCitedEvidence?: number;
  },
) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new AssistantEvidenceValidationError("Evidence receipt is invalid");
  }
  const raw = structuredClone(value) as Record<string, unknown>;
  const rawClaims = raw.claims;
  const lines = input.answer.split("\n");
  if (!Array.isArray(rawClaims) || rawClaims.length !== lines.length) {
    throw new AssistantEvidenceValidationError("Evidence receipt claims do not match answer");
  }
  const claims = rawClaims.map((claim, index) => {
    if (!claim || typeof claim !== "object" || Array.isArray(claim)) {
      throw new AssistantEvidenceValidationError("Evidence receipt claim is invalid");
    }
    return {
      text: lines[index],
      evidence_ids: (claim as Record<string, unknown>).evidence_ids,
    };
  });
  const expected = await buildAssistantEvidenceValidation(
    { claims }, input.availableEvidenceIds, {
      mode: input.mode,
      maxCitedEvidence: input.maxCitedEvidence,
    },
  );
  if (expected.answer !== input.answer) {
    throw new AssistantEvidenceValidationError("Evidence receipt answer is not canonical");
  }
  if (canonicalJson(raw) !== canonicalJson(expected.receipt)) {
    throw new AssistantEvidenceValidationError("Evidence receipt hash or fields are invalid");
  }
  return expected.receipt;
}
