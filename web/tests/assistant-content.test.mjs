import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  AssistantContentInputError,
  buildAssistantTextContentDocument,
  parseAssistantContentDocument,
  verifyAssistantContentDocument,
} from "../app/api/_shared/assistant-content.ts";


const fixture = () => JSON.parse(readFileSync(
  new URL("../../tests/fixtures/assistant_content_v1.json", import.meta.url),
  "utf8",
));
const evidenceIds = ["preview-evidence-1", "preview-evidence-2"];

test("shared Assistant content v1 fixture validates across the web boundary", async () => {
  const document = fixture();
  const answer = document.blocks[0].data.text;

  const parsed = await verifyAssistantContentDocument(document, { answer, evidenceIds });

  assert.equal(parsed.document_sha256, document.document_sha256);
  assert.deepEqual(parsed.blocks.map(block => block.type), [
    "markdown", "metric", "news_card", "news_card", "table", "callout",
  ]);
  parsed.blocks[0].data.text = "detached";
  assert.notEqual(document.blocks[0].data.text, "detached");
});

test("content validation rejects forged evidence, unsafe URLs, and hash drift", async () => {
  const cases = [
    document => { document.blocks[2].data.evidence_id = "foreign-evidence"; },
    document => { document.blocks[2].data.source_url = "javascript:alert(1)"; },
    document => { document.blocks[4].data.rows[0].push("extra"); },
    document => { document.blocks[5].component = "UnsafeWidget"; },
    document => { document.blocks[0].data.text = `${document.blocks[0].data.text}!`; },
  ];
  for (const mutate of cases) {
    const document = fixture();
    const answer = document.blocks[0].data.text;
    mutate(document);
    await assert.rejects(
      verifyAssistantContentDocument(document, { answer, evidenceIds }),
      AssistantContentInputError,
    );
  }
});

test("content text and time bounds stay stable across Python and TypeScript", () => {
  const unicodeDocument = fixture();
  unicodeDocument.blocks[2].data.source = "😀".repeat(100);
  assert.equal(parseAssistantContentDocument(unicodeDocument, {
    answer: unicodeDocument.blocks[0].data.text,
    evidenceIds,
  }).blocks[2].data.source, "😀".repeat(100));

  unicodeDocument.blocks[2].data.source += "😀";
  assert.throws(() => parseAssistantContentDocument(unicodeDocument, {
    answer: unicodeDocument.blocks[0].data.text,
    evidenceIds,
  }), AssistantContentInputError);

  const invalidTime = fixture();
  invalidTime.blocks[2].data.published_at = "2026-99-15T09:42:00.000Z";
  assert.throws(() => parseAssistantContentDocument(invalidTime, {
    answer: invalidTime.blocks[0].data.text,
    evidenceIds,
  }), AssistantContentInputError);
});

test("text-only content builder remains typed and never invents news cards", async () => {
  const answer = "当前没有足够的已收录证据支持具体市场解释。";
  const document = await buildAssistantTextContentDocument(answer, {
    insufficientEvidence: true,
  });

  assert.deepEqual(document.blocks.map(block => block.type), ["markdown", "callout"]);
  assert.equal(document.blocks[1].data.tone, "INSUFFICIENT_EVIDENCE");
  assert.deepEqual(
    parseAssistantContentDocument(document, { answer }).blocks,
    document.blocks,
  );
});
