import assert from "node:assert/strict";
import test from "node:test";

import {
  parseAssistantRoutingProvenance,
} from "../app/api/_shared/assistant-routing.ts";
import { assistantRouting } from "./assistant-routing-fixture.mjs";

test("routing provenance accepts one internally consistent bounded decision", () => {
  const routing = assistantRouting("NEWS_QA");

  assert.deepEqual(
    parseAssistantRoutingProvenance(routing, "NEWS_QA"),
    routing,
  );
});

test("routing provenance rejects task, effort, downgrade, and budget contradictions", () => {
  const cases = [
    assistantRouting("NEWS_QA", { task_type: "CONTEXT_COMPACTION" }),
    assistantRouting("NEWS_QA", { thinking_level: "MINIMAL" }),
    assistantRouting("NEWS_QA", { capacity_class: "SMALL" }),
    assistantRouting("NEWS_QA", { required_context_tokens: 99 }),
    assistantRouting("NEWS_QA", { context_limit: 1_024 }),
    assistantRouting("NEWS_QA", { selected_profile_id: "undeclared-profile" }),
    assistantRouting("NEWS_QA", { provider: "UNINSTALLED_PROVIDER" }),
    assistantRouting("CONVERSATION_TITLE", {
      reasoning_class: "ANALYTICAL",
      thinking_level: "HIGH",
      provider_thinking_level: "high",
      model_requirement: "LARGE_REQUIRED",
    }),
  ];

  for (const routing of cases) {
    assert.throws(
      () => parseAssistantRoutingProvenance(routing, "NEWS_QA"),
      /routing/i,
    );
  }
});

test("tool-heavy routing requires multiple planned calls and a large model", () => {
  const routing = assistantRouting("NEWS_QA", {
    reasoning_class: "TOOL_HEAVY",
    thinking_level: "HIGH",
    model_requirement: "LARGE_REQUIRED",
    planned_tool_calls: 3,
    supports_function_calling: true,
  });

  assert.equal(
    parseAssistantRoutingProvenance(routing, "NEWS_QA").planned_tool_calls,
    3,
  );
  assert.throws(
    () => parseAssistantRoutingProvenance(
      { ...routing, planned_tool_calls: 1 }, "NEWS_QA",
    ),
    /policy decision/i,
  );
});
