export function assistantRouting(taskType, overrides = {}) {
  const simple = taskType !== "NEWS_QA";
  const estimatedInputTokens = overrides.estimated_input_tokens ?? 1_000;
  const reservedOutputTokens = overrides.reserved_output_tokens ?? (
    taskType === "CONVERSATION_TITLE" ? 80 : taskType === "CONTEXT_COMPACTION" ? 2_400 : 1_200
  );
  return {
    policy_version: "assistant-routing-v1",
    task_type: taskType,
    reasoning_class: simple ? "SIMPLE" : "ANALYTICAL",
    thinking_level: simple ? "MINIMAL" : "HIGH",
    provider_thinking_level: simple ? "minimal" : "high",
    model_requirement: simple ? "SMALL_PREFERRED" : "LARGE_REQUIRED",
    estimated_input_tokens: estimatedInputTokens,
    reserved_output_tokens: reservedOutputTokens,
    required_context_tokens: estimatedInputTokens + reservedOutputTokens,
    planned_tool_calls: taskType === "NEWS_QA" ? 1 : 0,
    candidate_profile_ids: ["assistant-gemma-large-v1"],
    selected_profile_id: "assistant-gemma-large-v1",
    selected_model_id: "gemma-4-31b-it",
    provider: "GOOGLE_GENERATIVE_LANGUAGE",
    capacity_class: "LARGE",
    context_limit: 32_768,
    supports_thinking: true,
    supports_function_calling: false,
    supports_streaming: false,
    ...overrides,
  };
}
