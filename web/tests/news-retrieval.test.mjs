import assert from "node:assert/strict";
import test from "node:test";

import {
  buildNewsRetrievalSql,
  escapeSqlLike,
  parseNewsRetrievalRequest,
  retrieveNews,
} from "../app/api/_shared/news-retrieval.ts";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("retrieval-test", `${process.pid}-${Date.now()}`);
const { default: worker } = await import(workerUrl.href);

const executionContext = { waitUntil() {}, passThroughOnException() {} };
const assets = { fetch: async () => new Response("Not found", { status: 404 }) };

function requestFor(params = {}) {
  const url = new URL("https://example.test/api/news-search");
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  const parsed = parseNewsRetrievalRequest(url);
  assert.equal(parsed.ok, true, parsed.ok ? "" : parsed.error);
  return parsed.value;
}

function news(overrides = {}) {
  return {
    detail_key: "a".repeat(64),
    headline: "美联储讨论利率路径",
    source: "Reuters",
    category: "利率/Fed",
    source_published_time: "2026-08-03T10:00:00.000Z",
    collector_first_seen_time: "2026-08-03T10:01:00.000Z",
    emerging_topic_zh: "美国货币政策",
    impact_reason_zh: "利率预期影响美元和黄金",
    ...overrides,
  };
}

test("normalizes and bounds Chinese, multi-token, filter, and paging inputs", () => {
  const parsed = parseNewsRetrievalRequest(new URL(
    "https://example.test/api/news-search?"
      + new URLSearchParams({
        q: "  美联储   GOLD   ％ ＿ ＼ extra seventh ",
        page: "99999",
        limit: "999",
        published_from: "2026-08-01",
        published_to: "2026-08-02",
        received_from: "2026-08-01T01:00:00+08:00",
        source: "  Reuters  ",
        category: " 利率/Fed ",
      }),
  ));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.value.query, "美联储 GOLD % _ \\ extra");
  assert.deepEqual(parsed.value.tokens, ["美联储", "gold", "%", "_", "\\", "extra"]);
  assert.equal(parsed.value.page, 1_000);
  assert.equal(parsed.value.pageSize, 20);
  assert.equal(parsed.value.filters.published_from, "2026-08-01T00:00:00.000Z");
  assert.equal(parsed.value.filters.published_to, "2026-08-02T23:59:59.999Z");
  assert.equal(parsed.value.filters.received_from, "2026-07-31T17:00:00.000Z");
  assert.equal(parsed.value.filters.source, "Reuters");
  assert.equal(parsed.value.filters.category, "利率/Fed");
});

test("rejects invalid dates, reversed ranges, and malformed evidence ids", () => {
  for (const [params, code] of [
    [{ published_from: "2026-02-30" }, "INVALID_PUBLISHED_FROM"],
    [{ published_from: "2026-08-03", published_to: "2026-08-01" }, "INVALID_PUBLISHED_RANGE"],
    [{ received_from: "not-a-date" }, "INVALID_RECEIVED_FROM"],
    [{ evidence_id: "bad id" }, "INVALID_EVIDENCE_ID"],
  ]) {
    const url = new URL("https://example.test/api/news-search");
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
    const parsed = parseNewsRetrievalRequest(url);
    assert.equal(parsed.ok, false);
    assert.equal(parsed.code, code);
    assert.equal(parsed.status, 400);
  }
});

test("builds one escaped bounded D1 query contract for every caller", () => {
  assert.equal(escapeSqlLike("a%b_c\\d"), "a\\%b\\_c\\\\d");
  const request = requestFor({
    q: "% _ \\",
    published_from: "2026-08-01",
    published_to: "2026-08-02",
    received_from: "2026-08-01",
    received_to: "2026-08-03",
    evidence_id: "a".repeat(64),
    source: "Reuters",
    category: "利率/Fed",
  });
  const plan = buildNewsRetrievalSql(request);
  assert.equal((plan.whereSql.match(/LIKE \? ESCAPE/g) ?? []).length, 3);
  assert.match(plan.whereSql, /julianday\(published_time\) >= julianday\(\?\)/);
  assert.match(plan.whereSql, /julianday\(collector_first_seen_time\) <= julianday\(\?\)/);
  assert.match(plan.whereSql, /detail_key = \?/);
  assert.match(plan.whereSql, /json_extract\(payload,'\$\.source'\)/);
  assert.match(plan.whereSql, /lower\(category\) = \?/);
  assert.deepEqual(plan.bindings.slice(0, 3), ["%\\%%", "%\\_%", "%\\\\%"]);
  assert.equal(plan.bindings.at(-2), "reuters");
  assert.equal(plan.bindings.at(-1), "利率/fed");
});

test("applies identical Chinese, literal special-character, date, and metadata rules to Preview", async () => {
  const later = news({
    detail_key: "b".repeat(64),
    headline: "美联储：利率路径可能保持 100%_谨慎\\",
    impact_reason_zh: "与已有报道1d181c31完全一致。",
  });
  const earlier = news({
    detail_key: "a".repeat(64),
    headline: "美联储更新利率路径 100%_谨慎\\",
  });
  const wrongSource = news({
    detail_key: "c".repeat(64),
    source: "Other",
    headline: "美联储利率路径 100%_谨慎\\",
  });
  const request = requestFor({
    q: "美联储 100%_谨慎\\",
    published_from: "2026-08-03",
    published_to: "2026-08-03",
    received_from: "2026-08-03",
    received_to: "2026-08-03",
    source: "Reuters",
    category: "利率/Fed",
    limit: "1",
  });

  const first = await retrieveNews({ request, previewItems: [earlier, wrongSource, later] });
  assert.equal(first.ok, true);
  assert.equal(first.payload.source_mode, "IMMUTABLE_PREVIEW_SNAPSHOT");
  assert.equal(first.payload.archive_complete, false);
  assert.equal(first.payload.total, 2);
  assert.equal(first.payload.has_more, true);
  assert.equal(first.payload.items[0].evidence_id, "b".repeat(64));
  assert.equal(
    first.payload.items[0].impact_reason_zh,
    "与系统中已有的一篇报道完全一致。",
  );
  assert.deepEqual(first.payload.retrieval.canonical_evidence_ids, ["b".repeat(64)]);
  assert.equal(first.payload.retrieval.fallback_reason, "AUTHORITATIVE_STORE_UNAVAILABLE");

  const second = await retrieveNews({
    request: { ...request, page: 2 },
    previewItems: [earlier, wrongSource, later],
  });
  assert.equal(second.ok, true);
  assert.equal(second.payload.items[0].evidence_id, "a".repeat(64));
  assert.equal(second.payload.has_more, false);
});

test("filters exact evidence ids and received-time ranges", async () => {
  const target = news({ detail_key: "d".repeat(64) });
  const request = requestFor({
    evidence_id: "d".repeat(64),
    received_from: "2026-08-03T10:00:30Z",
    received_to: "2026-08-03T10:01:30Z",
  });
  const outcome = await retrieveNews({
    request,
    previewItems: [target, news({ detail_key: "e".repeat(64) })],
  });
  assert.equal(outcome.ok, true);
  assert.deepEqual(outcome.payload.retrieval.canonical_evidence_ids, ["d".repeat(64)]);
});

test("uses the D1 archive before Preview fallback and preserves database evidence ids", async () => {
  const statements = [];
  const databaseId = "f".repeat(64);
  const binding = {
    prepare(sql) {
      return {
        bind(...bindings) {
          statements.push({ sql, bindings });
          return {
            async all() {
              return { results: [{ payload: JSON.stringify(news({ detail_key: "wrong" })), detail_key: databaseId }] };
            },
            async first() { return { count: 1 }; },
          };
        },
      };
    },
  };
  const outcome = await retrieveNews({
    binding,
    request: requestFor({ q: "美联储" }),
    previewItems: [],
  });
  assert.equal(outcome.ok, true);
  assert.equal(outcome.payload.source_mode, "READ_ONLY_D1_ARCHIVE");
  assert.equal(outcome.payload.archive_complete, true);
  assert.equal(outcome.payload.items[0].evidence_id, databaseId);
  assert.match(statements[0].sql, /ORDER BY published_time DESC, collector_first_seen_time DESC, detail_key DESC/);
  assert.match(statements[0].sql, /LIMIT \? OFFSET \?/);
  assert.equal(statements.length, 2);
});

test("labels Preview fallback and returns explicit unavailability outside Preview", async () => {
  const failingBinding = { prepare() { throw new Error("D1 offline"); } };
  const request = requestFor({ q: "美联储" });
  const preview = await retrieveNews({
    binding: failingBinding,
    request,
    previewItems: [news()],
  });
  assert.equal(preview.ok, true);
  assert.equal(preview.payload.source_mode, "IMMUTABLE_PREVIEW_SNAPSHOT");

  const unavailable = await retrieveNews({ binding: failingBinding, request });
  assert.deepEqual(unavailable, {
    ok: false,
    status: 503,
    code: "NEWS_RETRIEVAL_UNAVAILABLE",
    error: "新闻搜索暂不可用",
  });
});

test("does not enumerate the archive without a query or filter", async () => {
  let touched = false;
  const binding = { prepare() { touched = true; throw new Error("must not query"); } };
  const outcome = await retrieveNews({
    binding,
    request: requestFor(),
    previewItems: [news()],
  });
  assert.equal(outcome.ok, true);
  assert.equal(outcome.payload.source_mode, "NOT_QUERIED");
  assert.equal(outcome.payload.total, 0);
  assert.equal(touched, false);
});

test("rejects invalid HTTP date filters before touching D1", async () => {
  let touched = false;
  const response = await worker.fetch(
    new Request("http://localhost/api/news-search?q=gold&published_from=2026-02-30"),
    {
      DB: { prepare() { touched = true; throw new Error("must not query"); } },
      ASSETS: assets,
    },
    executionContext,
  );
  assert.equal(response.status, 400);
  assert.equal((await response.json()).code, "INVALID_PUBLISHED_FROM");
  assert.equal(touched, false);
});
