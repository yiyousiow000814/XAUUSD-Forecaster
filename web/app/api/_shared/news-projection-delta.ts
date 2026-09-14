/** Atomic sparse publication against an acknowledged complete current baseline. */
import { ACTIVE_NEWS_SQL, NEWS_REVIEW_STATE_INVARIANT_SQL } from "../../_lib/news-review-state";
import {
  NEWS_DELTA_CONTRACT, NEWS_GENERATION_ID, NEWS_PROJECTION_CONTRACT_VERSION,
  NewsProjectionProtocolError, newsProjectionPayloadHash, newsProjectionCountsStatement,
  readNewsProjectionState, validDetail, validIndex, validateNewsProjectionManifest,
  jsonValuesEqual, supportsNewsDelta,
  type NewsProjectionDetailItem, type NewsProjectionIndexItem,
} from "./news-projection-store";

export type NewsDelta = {
  base: { generation_id: string; snapshot_id: string; receipt_digest: string };
  source: ReturnType<typeof validateNewsProjectionManifest>;
  indexes: NewsProjectionIndexItem[];
  details: NewsProjectionDetailItem[];
  removed: string[];
};

function invalid(message: string): never {
  throw new NewsProjectionProtocolError(message, 400, "NEWS_DELTA_INVALID");
}

export async function applyNewsProjectionDelta(binding: D1Database, raw: unknown, digest: unknown) {
  const patch = raw as NewsDelta;
  if (!patch || !patch.base || ![patch.base.generation_id, patch.base.snapshot_id,
    patch.base.receipt_digest].every(v => typeof v === "string" && NEWS_GENERATION_ID.test(v))
    || !Array.isArray(patch.indexes) || patch.indexes.length > 32
    || !Array.isArray(patch.details) || patch.details.length > 8
    || !Array.isArray(patch.removed) || patch.removed.length > 32
    || !patch.indexes.every(v => v && validIndex(v) && v.mirror_contract === NEWS_PROJECTION_CONTRACT_VERSION)
    || !patch.details.every(v => v && validDetail(v))
    || !patch.removed.every(v => typeof v === "string" && NEWS_GENERATION_ID.test(v))) {
    invalid("invalid sparse news patch");
  }
  const source = validateNewsProjectionManifest(patch.source);
  if (source.expected_detail_count !== source.expected_index_count) invalid("incomplete source membership");
  const indexKeys = patch.indexes.map(v => String(v.detail_key));
  const detailKeys = patch.details.map(v => String(v.detail_key));
  if (new Set([...indexKeys, ...patch.removed]).size !== indexKeys.length + patch.removed.length
    || new Set(detailKeys).size !== detailKeys.length
    || detailKeys.some(key => !indexKeys.includes(key))) invalid("duplicate or unreferenced patch identity");
  const computed = await newsProjectionPayloadHash(patch);
  if (digest !== computed) invalid("patch digest mismatch");
  const manifest = { ...source, contract_version: NEWS_DELTA_CONTRACT,
    generation_id: computed, expected_receipt_digest: computed };
  const active = await readNewsProjectionState(binding);
  if (!await supportsNewsDelta(binding)) {
    throw new NewsProjectionProtocolError("news delta migration is not installed", 503, "NEWS_DELTA_SCHEMA_MISSING");
  }
  const acknowledgement = { status: "OK", applied: computed, manifest };
  if (active?.active_generation_id === computed && active.receipt_digest === computed
    && active.projection_state === "CURRENT") return acknowledgement;
  if (!active || active.projection_state !== "CURRENT"
    || active.active_generation_id !== patch.base.generation_id
    || active.snapshot_id !== patch.base.snapshot_id || active.receipt_digest !== patch.base.receipt_digest
    || ![NEWS_PROJECTION_CONTRACT_VERSION, NEWS_DELTA_CONTRACT].includes(active.contract_version)) {
    throw new NewsProjectionProtocolError("news delta baseline changed", 409, "NEWS_DELTA_BASE_CHANGED");
  }
  const now = new Date().toISOString();
  if (await binding.prepare("SELECT generation_id FROM news_projection_generations WHERE state='STAGING' LIMIT 1").first()) {
    throw new NewsProjectionProtocolError("news full replay is in progress", 409, "NEWS_DELTA_BASE_CHANGED");
  }
  const indexes = JSON.stringify(patch.indexes);
  const removed = JSON.stringify(patch.removed);
  const current = `SELECT ? generation_id,* FROM news_index WHERE ${ACTIVE_NEWS_SQL}`;
  const serializedDetails: string[] = [];
  for (const item of patch.details) {
    const prior = await binding.prepare("SELECT detail_hash,payload FROM news_details WHERE detail_key=?")
      .bind(item.detail_key).first<{ detail_hash: string; payload: string }>();
    if (prior && (prior.detail_hash !== item.detail_hash || !jsonValuesEqual(JSON.parse(prior.payload), item.payload))) {
      invalid("immutable news detail changed");
    }
    serializedDetails.push(prior?.payload ?? JSON.stringify(item.payload));
  }
  // D1 batch is one transaction. Guards deliberately raise inside that
  // transaction, including failures discovered after index mutations.
  const statements = [
    binding.prepare(`SELECT CASE WHEN EXISTS (
      SELECT 1 FROM news_projection_state WHERE id=1 AND projection_state='CURRENT'
        AND active_generation_id=? AND snapshot_id=? AND receipt_digest=?
        AND missing_detail_count=0 AND invariant_violation_count=0
        AND EXISTS (SELECT 1 FROM news_projection_generations g
          WHERE g.generation_id=? AND julianday(g.watermark)<=julianday(?))
      ) AND NOT EXISTS (SELECT 1 FROM news_projection_generations WHERE state='STAGING')
      THEN 1 ELSE json('NEWS_DELTA_BASE_CHANGED') END`).bind(
      patch.base.generation_id, patch.base.snapshot_id, patch.base.receipt_digest,
      patch.base.generation_id, source.watermark),
    // This transient state is invisible outside the atomic batch. Existing
    // ownership triggers allow only the publication owner to replace rows.
    binding.prepare("UPDATE news_projection_state SET projection_state='REPLAYING' WHERE id=1"),
    binding.prepare(`SELECT CASE WHEN NOT EXISTS (
      SELECT 1 FROM json_each(?) r WHERE NOT EXISTS (
        SELECT 1 FROM news_index n WHERE n.detail_key=r.value AND ${ACTIVE_NEWS_SQL.replaceAll("payload", "n.payload")}
      )) THEN 1 ELSE json('NEWS_DELTA_REMOVAL_MISSING') END`).bind(removed),
    ...patch.details.flatMap((item, i) => [binding.prepare(
      `INSERT INTO news_details(detail_key,detail_hash,payload,received_at) VALUES (?,?,?,?)
       ON CONFLICT(detail_key) DO NOTHING`,
    ).bind(item.detail_key, item.detail_hash, serializedDetails[i], now),
    binding.prepare(`SELECT CASE WHEN EXISTS (SELECT 1 FROM news_details
      WHERE detail_key=? AND detail_hash=? AND payload=?) THEN 1 ELSE json('NEWS_DELTA_DETAIL_CHANGED') END`)
      .bind(item.detail_key, item.detail_hash, serializedDetails[i])]),
    binding.prepare(`UPDATE news_index SET parsed=0,model_candidate=0,
      payload=json_set(payload,'$.annotation_status','SUPERSEDED_CONTRACT',
        '$.model_visibility','MODEL_INELIGIBLE','$.parsed_at',json('null'))
      WHERE detail_key IN (SELECT value FROM json_each(?))`).bind(removed),
    binding.prepare(`INSERT INTO news_index(detail_key,category,cluster_id,published_time,
      collector_first_seen_time,parsed,model_candidate,impact_expires_at,mirror_contract,payload,received_at)
      SELECT json_extract(value,'$.detail_key'),json_extract(value,'$.category'),
        json_extract(value,'$.cluster_id'),coalesce(json_extract(value,'$.source_published_time'),
          json_extract(value,'$.collector_first_seen_time')),json_extract(value,'$.collector_first_seen_time'),
        CASE WHEN json_type(value,'$.parsed_at')='text' THEN 1 ELSE 0 END,
        CASE WHEN json_extract(value,'$.model_visibility')='MODEL_VISIBLE' THEN 1 ELSE 0 END,
        json_extract(value,'$.impact_expires_at'),json_extract(value,'$.mirror_contract'),value,?
      FROM json_each(?) WHERE true
      ON CONFLICT(detail_key) DO UPDATE SET category=excluded.category,cluster_id=excluded.cluster_id,
        published_time=excluded.published_time,collector_first_seen_time=excluded.collector_first_seen_time,
        parsed=excluded.parsed,model_candidate=excluded.model_candidate,impact_expires_at=excluded.impact_expires_at,
        mirror_contract=excluded.mirror_contract,payload=excluded.payload,received_at=excluded.received_at`).bind(now, indexes),
    binding.prepare(`SELECT CASE WHEN count(*)=? AND count(DISTINCT cluster_id)=count(*)
      AND coalesce(sum(CASE WHEN (${NEWS_REVIEW_STATE_INVARIANT_SQL})
        AND EXISTS (SELECT 1 FROM news_details d WHERE d.detail_key=news_index.detail_key)
        THEN 0 ELSE 1 END),0)=0 THEN 1 ELSE json('NEWS_DELTA_INVARIANT_FAILED') END
      FROM news_index WHERE ${ACTIVE_NEWS_SQL}`).bind(source.expected_index_count),
    // Metadata cleanup never removes current/preceding data or full receipts.
    ...["news_projection_counts", "news_projection_generations"].map(table => binding.prepare(
      `DELETE FROM ${table} WHERE generation_id IN (
        SELECT generation_id FROM news_projection_generations WHERE state='SUPERSEDED'
          AND contract_version=? AND generation_id<>?)`,
    ).bind(NEWS_DELTA_CONTRACT, patch.base.generation_id)),
    binding.prepare(`UPDATE news_projection_generations SET state='SUPERSEDED',updated_at=? WHERE state='CURRENT'`).bind(now),
    binding.prepare(`INSERT INTO news_projection_generations
      (generation_id,snapshot_id,state,contract_version,window_start,watermark,
       expected_index_count,expected_detail_count,withdrawal_count,source_digest,
       expected_receipt_digest,receipt_digest,next_detail_offset,next_index_offset,
       staged_detail_count,staged_index_count,missing_detail_count,invariant_violation_count,
       created_at,updated_at,activated_at)
      VALUES (?,?,'CURRENT',?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?)`).bind(
      computed,source.snapshot_id,NEWS_DELTA_CONTRACT,source.window_start,source.watermark,
      source.expected_index_count,source.expected_detail_count,source.withdrawal_count,source.source_digest,
      computed,computed,source.expected_detail_count,source.expected_index_count,
      source.expected_detail_count,source.expected_index_count,now,now,now),
    newsProjectionCountsStatement(binding, computed, current),
    binding.prepare(`UPDATE news_projection_state SET active_generation_id=?,snapshot_id=?,
      contract_version=?,source_digest=?,receipt_digest=?,index_count=?,detail_count=?,
      missing_detail_count=0,invariant_violation_count=0,projection_state='CURRENT',
      activated_at=?,verified_at=? WHERE id=1`).bind(computed,source.snapshot_id,NEWS_DELTA_CONTRACT,
      source.source_digest,computed,source.expected_index_count,source.expected_detail_count,now,now),
  ];
  try {
    await binding.batch(statements);
  } catch (error) {
    const latest = await readNewsProjectionState(binding);
    const staging = await binding.prepare("SELECT generation_id FROM news_projection_generations WHERE state='STAGING' LIMIT 1").first();
    if (latest?.active_generation_id !== patch.base.generation_id || staging) {
      throw new NewsProjectionProtocolError("news delta baseline changed", 409, "NEWS_DELTA_BASE_CHANGED");
    }
    throw error;
  }
  return acknowledgement;
}
