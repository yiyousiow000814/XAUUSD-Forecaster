export const ASSISTANT_ROUTING_POLICY_VERSION = "assistant-routing-v1";
export const ASSISTANT_CAPACITY_POLICY_VERSION = "assistant-capacity-v1";

export type AssistantCapacityProvenance = {
  policy_version: string;
  service_priority: "INTERACTIVE" | "BACKGROUND";
  selected_pool_fingerprint: string;
  selected_pool_type: "PREEMPTIBLE" | "ROUTINE";
  candidate_pool_count: number;
  candidate_pair_count: number;
  attempt_count: number;
  estimated_input_tokens: number;
  soft_cap_basis_points: number;
  max_in_flight: number;
  policy_source: "CONFIGURED" | "REGISTRY_DEFAULT";
  model_fallback_used: boolean;
};

export type AssistantRoutingTask =
  | "NEWS_QA"
  | "CONVERSATION_TITLE"
  | "CONTEXT_COMPACTION";

export type AssistantRoutingProvenance = {
  policy_version: string;
  task_type: AssistantRoutingTask;
  reasoning_class: "SIMPLE" | "ANALYTICAL" | "TOOL_HEAVY";
  thinking_level: "MINIMAL" | "HIGH";
  provider_thinking_level: "minimal" | "high" | null;
  model_requirement: "SMALL_PREFERRED" | "LARGE_REQUIRED";
  estimated_input_tokens: number;
  reserved_output_tokens: number;
  required_context_tokens: number;
  planned_tool_calls: number;
  candidate_profile_ids: string[];
  selected_profile_id: string;
  selected_model_id: string;
  provider: string;
  capacity_class: "SMALL" | "LARGE";
  context_limit: number;
  supports_thinking: boolean;
  supports_function_calling: boolean;
  supports_streaming: boolean;
  capacity: AssistantCapacityProvenance;
};

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/;
const modelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/;
const providerIdentifier = /^[A-Z][A-Z0-9_]{2,63}$/;
const tasks = new Set<AssistantRoutingTask>([
  "NEWS_QA", "CONVERSATION_TITLE", "CONTEXT_COMPACTION",
]);
const reasoningClasses = new Set(["SIMPLE", "ANALYTICAL", "TOOL_HEAVY"]);
const thinkingLevels = new Set(["MINIMAL", "HIGH"]);
const modelRequirements = new Set(["SMALL_PREFERRED", "LARGE_REQUIRED"]);
const capacityClasses = new Set(["SMALL", "LARGE"]);
const installedProvider = "GOOGLE_GENERATIVE_LANGUAGE";
const poolFingerprint = /^[a-f0-9]{16}$/;
const servicePriorities = new Set(["INTERACTIVE", "BACKGROUND"]);
const poolTypes = new Set(["PREEMPTIBLE", "ROUTINE"]);
const policySources = new Set(["CONFIGURED", "REGISTRY_DEFAULT"]);

const boundedInteger = (value: unknown, minimum: number, maximum: number) => (
  typeof value === "number" && Number.isSafeInteger(value)
    && value >= minimum && value <= maximum
);

export function parseAssistantRoutingProvenance(
  value: unknown,
  expectedTask?: AssistantRoutingTask,
): AssistantRoutingProvenance {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Assistant routing provenance is required");
  }
  const raw = value as Record<string, unknown>;
  const policyVersion = String(raw.policy_version ?? "").trim();
  const taskType = String(raw.task_type ?? "").trim() as AssistantRoutingTask;
  const reasoningClass = String(raw.reasoning_class ?? "").trim();
  const thinkingLevel = String(raw.thinking_level ?? "").trim();
  const providerThinkingLevel = raw.provider_thinking_level == null
    ? null : String(raw.provider_thinking_level).trim();
  const modelRequirement = String(raw.model_requirement ?? "").trim();
  const selectedProfileId = String(raw.selected_profile_id ?? "").trim();
  const selectedModelId = String(raw.selected_model_id ?? "").trim();
  const provider = String(raw.provider ?? "").trim();
  const capacityClass = String(raw.capacity_class ?? "").trim();
  const candidateProfileIds = raw.candidate_profile_ids;
  if (
    policyVersion !== ASSISTANT_ROUTING_POLICY_VERSION
    || !tasks.has(taskType)
    || (expectedTask && taskType !== expectedTask)
    || !reasoningClasses.has(reasoningClass)
    || !thinkingLevels.has(thinkingLevel)
    || (providerThinkingLevel !== null
      && !new Set(["minimal", "high"]).has(providerThinkingLevel))
    || !modelRequirements.has(modelRequirement)
    || !identifier.test(selectedProfileId)
    || !modelIdentifier.test(selectedModelId)
    || !providerIdentifier.test(provider)
    || provider !== installedProvider
    || !capacityClasses.has(capacityClass)
    || typeof raw.supports_thinking !== "boolean"
    || typeof raw.supports_function_calling !== "boolean"
    || typeof raw.supports_streaming !== "boolean"
    || !Array.isArray(candidateProfileIds)
    || candidateProfileIds.length === 0
    || candidateProfileIds.length > 8
  ) throw new Error("Assistant routing provenance is invalid");
  const normalizedCandidates = candidateProfileIds.map(item => String(item ?? "").trim());
  if (
    normalizedCandidates.some(item => !identifier.test(item))
    || new Set(normalizedCandidates).size !== normalizedCandidates.length
    || !normalizedCandidates.includes(selectedProfileId)
  ) throw new Error("Assistant routing model candidates are invalid");
  const estimatedInputTokens = typeof raw.estimated_input_tokens === "number"
    ? raw.estimated_input_tokens : Number.NaN;
  const reservedOutputTokens = typeof raw.reserved_output_tokens === "number"
    ? raw.reserved_output_tokens : Number.NaN;
  const requiredContextTokens = typeof raw.required_context_tokens === "number"
    ? raw.required_context_tokens : Number.NaN;
  const plannedToolCalls = typeof raw.planned_tool_calls === "number"
    ? raw.planned_tool_calls : Number.NaN;
  const contextLimit = typeof raw.context_limit === "number"
    ? raw.context_limit : Number.NaN;
  const rawCapacity = raw.capacity;
  if (
    !boundedInteger(estimatedInputTokens, 1, 1_000_000)
    || !boundedInteger(reservedOutputTokens, 1, 1_000_000)
    || !boundedInteger(requiredContextTokens, 2, 1_000_000)
    || !boundedInteger(plannedToolCalls, 0, 64)
    || !boundedInteger(contextLimit, 1_024, 1_000_000)
    || requiredContextTokens !== estimatedInputTokens + reservedOutputTokens
    || contextLimit < requiredContextTokens
  ) throw new Error("Assistant routing token budget is invalid");
  if (!rawCapacity || typeof rawCapacity !== "object" || Array.isArray(rawCapacity)) {
    throw new Error("Assistant capacity provenance is required");
  }
  const capacityValue = rawCapacity as Record<string, unknown>;
  const capacityPolicyVersion = String(capacityValue.policy_version ?? "").trim();
  const servicePriority = String(capacityValue.service_priority ?? "").trim();
  const selectedPoolFingerprint = String(
    capacityValue.selected_pool_fingerprint ?? "",
  ).trim();
  const selectedPoolType = String(capacityValue.selected_pool_type ?? "").trim();
  const policySource = String(capacityValue.policy_source ?? "").trim();
  const candidatePoolCount = typeof capacityValue.candidate_pool_count === "number"
    ? capacityValue.candidate_pool_count : Number.NaN;
  const candidatePairCount = typeof capacityValue.candidate_pair_count === "number"
    ? capacityValue.candidate_pair_count : Number.NaN;
  const attemptCount = typeof capacityValue.attempt_count === "number"
    ? capacityValue.attempt_count : Number.NaN;
  const capacityEstimatedInputTokens = typeof capacityValue.estimated_input_tokens === "number"
    ? capacityValue.estimated_input_tokens : Number.NaN;
  const softCapBasisPoints = typeof capacityValue.soft_cap_basis_points === "number"
    ? capacityValue.soft_cap_basis_points : Number.NaN;
  const maxInFlight = typeof capacityValue.max_in_flight === "number"
    ? capacityValue.max_in_flight : Number.NaN;
  if (
    capacityPolicyVersion !== ASSISTANT_CAPACITY_POLICY_VERSION
    || !servicePriorities.has(servicePriority)
    || !poolFingerprint.test(selectedPoolFingerprint)
    || !poolTypes.has(selectedPoolType)
    || !policySources.has(policySource)
    || !boundedInteger(candidatePoolCount, 1, 16)
    || !boundedInteger(candidatePairCount, 1, 128)
    || !boundedInteger(attemptCount, 1, 128)
    || !boundedInteger(capacityEstimatedInputTokens, 1, 1_000_000)
    || !boundedInteger(softCapBasisPoints, 1, 10_000)
    || !boundedInteger(maxInFlight, 1, 1_000)
    || typeof capacityValue.model_fallback_used !== "boolean"
    || candidatePoolCount > candidatePairCount
    || capacityEstimatedInputTokens !== estimatedInputTokens
    || (servicePriority === "BACKGROUND" && selectedPoolType !== "ROUTINE")
    || capacityValue.model_fallback_used
      !== (normalizedCandidates.indexOf(selectedProfileId) > 0)
  ) throw new Error("Assistant capacity provenance is invalid");
  if (
    (reasoningClass === "SIMPLE" && thinkingLevel !== "MINIMAL")
    || (reasoningClass !== "SIMPLE" && thinkingLevel !== "HIGH")
    || (reasoningClass === "TOOL_HEAVY" && plannedToolCalls <= 1)
    || (reasoningClass === "TOOL_HEAVY" && raw.supports_function_calling !== true)
    || (reasoningClass !== "SIMPLE" && raw.supports_thinking !== true)
    || (raw.supports_thinking === false && providerThinkingLevel !== null)
    || (raw.supports_thinking === true
      && providerThinkingLevel !== thinkingLevel.toLowerCase())
    || (modelRequirement === "LARGE_REQUIRED" && capacityClass !== "LARGE")
    || (reasoningClass !== "SIMPLE" && modelRequirement !== "LARGE_REQUIRED")
    || (taskType !== "NEWS_QA" && (
      reasoningClass !== "SIMPLE"
      || modelRequirement !== "SMALL_PREFERRED"
      || plannedToolCalls !== 0
    ))
  ) throw new Error("Assistant routing policy decision is inconsistent");
  return {
    policy_version: policyVersion,
    task_type: taskType,
    reasoning_class: reasoningClass as AssistantRoutingProvenance["reasoning_class"],
    thinking_level: thinkingLevel as AssistantRoutingProvenance["thinking_level"],
    provider_thinking_level: providerThinkingLevel as AssistantRoutingProvenance["provider_thinking_level"],
    model_requirement: modelRequirement as AssistantRoutingProvenance["model_requirement"],
    estimated_input_tokens: estimatedInputTokens,
    reserved_output_tokens: reservedOutputTokens,
    required_context_tokens: requiredContextTokens,
    planned_tool_calls: plannedToolCalls,
    candidate_profile_ids: normalizedCandidates,
    selected_profile_id: selectedProfileId,
    selected_model_id: selectedModelId,
    provider,
    capacity_class: capacityClass as AssistantRoutingProvenance["capacity_class"],
    context_limit: contextLimit,
    supports_thinking: raw.supports_thinking,
    supports_function_calling: raw.supports_function_calling,
    supports_streaming: raw.supports_streaming,
    capacity: {
      policy_version: capacityPolicyVersion,
      service_priority: servicePriority as AssistantCapacityProvenance["service_priority"],
      selected_pool_fingerprint: selectedPoolFingerprint,
      selected_pool_type: selectedPoolType as AssistantCapacityProvenance["selected_pool_type"],
      candidate_pool_count: candidatePoolCount as number,
      candidate_pair_count: candidatePairCount as number,
      attempt_count: attemptCount as number,
      estimated_input_tokens: capacityEstimatedInputTokens as number,
      soft_cap_basis_points: softCapBasisPoints as number,
      max_in_flight: maxInFlight as number,
      policy_source: policySource as AssistantCapacityProvenance["policy_source"],
      model_fallback_used: capacityValue.model_fallback_used,
    },
  };
}
