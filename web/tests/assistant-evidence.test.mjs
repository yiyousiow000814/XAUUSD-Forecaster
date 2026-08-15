import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  AssistantEvidenceValidationError,
  buildAssistantEvidenceValidation,
  parseAssistantEvidenceReceipt,
} from "../app/api/_shared/assistant-evidence.ts";


const cases = JSON.parse(readFileSync(
  new URL("../../tests/fixtures/assistant_evidence_validation.json", import.meta.url),
  "utf8",
)).cases;

test("TypeScript evidence receipts match the Python contract fixture", async () => {
  for (const item of cases) {
    const result = await buildAssistantEvidenceValidation(
      item.input,
      item.available_evidence_ids,
      { mode: item.mode, maxCitedEvidence: item.max_cited_evidence },
    );
    assert.equal(result.answer, item.answer, item.name);
    assert.deepEqual(result.evidenceIds, item.evidence_ids, item.name);
    assert.deepEqual(result.receipt, item.receipt, item.name);
    assert.deepEqual(await parseAssistantEvidenceReceipt(item.receipt, {
      answer: item.answer,
      availableEvidenceIds: item.available_evidence_ids,
      mode: item.mode,
      maxCitedEvidence: item.max_cited_evidence,
    }), item.receipt, item.name);
  }
});

test("receipt verification rejects hash drift and non-canonical answers", async () => {
  const item = cases[0];
  const drifted = structuredClone(item.receipt);
  drifted.claims[0].evidence_ids = ["ev:gold-2"];
  await assert.rejects(
    parseAssistantEvidenceReceipt(drifted, {
      answer: item.answer,
      availableEvidenceIds: item.available_evidence_ids,
      mode: item.mode,
      maxCitedEvidence: item.max_cited_evidence,
    }),
    AssistantEvidenceValidationError,
  );
  await assert.rejects(
    parseAssistantEvidenceReceipt(item.receipt, {
      answer: ` ${item.answer}`,
      availableEvidenceIds: item.available_evidence_ids,
      mode: item.mode,
      maxCitedEvidence: item.max_cited_evidence,
    }),
    AssistantEvidenceValidationError,
  );
});

test("available packet bounds stay separate from cited coverage", async () => {
  const available = Array.from({ length: 20 }, (_, index) => `evidence-${index}`);
  const result = await buildAssistantEvidenceValidation({
    claims: [{ text: "只引用一项。", evidence_ids: [available[0]] }],
  }, available, { maxCitedEvidence: 12 });
  assert.deepEqual(result.evidenceIds, [available[0]]);
  assert.deepEqual(result.receipt.available_evidence_ids, available);

  await assert.rejects(buildAssistantEvidenceValidation({
    claims: [{ text: "没有引用。", evidence_ids: [] }],
  }, available, { maxCitedEvidence: 12 }), AssistantEvidenceValidationError);

  await assert.rejects(buildAssistantEvidenceValidation({
    claims: [{ text: "未知模式。", evidence_ids: [] }],
  }, [], { mode: "UNKNOWN" }), AssistantEvidenceValidationError);
});
