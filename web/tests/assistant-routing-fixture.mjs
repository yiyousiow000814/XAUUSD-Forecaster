export function assistantRouting(taskType, overrides = {}) {
  const simple = !new Set(["NEWS_QA", "ASSISTANT_CHAT"]).has(taskType);
  const estimatedInputTokens = overrides.estimated_input_tokens ?? 1_000;
  const reservedOutputTokens = overrides.reserved_output_tokens ?? (
    taskType === "CONVERSATION_TITLE" ? 80 : taskType === "CONTEXT_COMPACTION" ? 2_400 : 1_200
  );
  const base = {
    policy_version: "assistant-routing-v2",
    task_type: taskType,
    reasoning_class: simple ? "SIMPLE" : "ANALYTICAL",
    thinking_level: simple ? "MINIMAL" : "HIGH",
    provider_thinking_level: simple ? "minimal" : "high",
    model_requirement: simple ? "SMALL_PREFERRED" : "LARGE_REQUIRED",
    estimated_input_tokens: estimatedInputTokens,
    reserved_output_tokens: reservedOutputTokens,
    required_context_tokens: estimatedInputTokens + reservedOutputTokens,
    planned_tool_calls: 0,
    candidate_profile_ids: ["assistant-gemma-large-v1"],
    selected_profile_id: "assistant-gemma-large-v1",
    selected_model_id: "gemma-4-31b-it",
    provider: "GOOGLE_GENERATIVE_LANGUAGE",
    capacity_class: "LARGE",
    context_limit: 32_768,
    supports_thinking: true,
    supports_function_calling: true,
    supports_streaming: false,
    capacity: {
      policy_version: "assistant-capacity-v1",
      service_priority: simple ? "BACKGROUND" : "INTERACTIVE",
      selected_pool_fingerprint: "0123456789abcdef",
      selected_pool_type: simple ? "ROUTINE" : "PREEMPTIBLE",
      candidate_pool_count: 2,
      candidate_pair_count: 2,
      attempt_count: 1,
      estimated_input_tokens: estimatedInputTokens,
      soft_cap_basis_points: 8_000,
      max_in_flight: 2,
      policy_source: "CONFIGURED",
      model_fallback_used: false,
    },
    ...overrides,
  };
  return base;
}
