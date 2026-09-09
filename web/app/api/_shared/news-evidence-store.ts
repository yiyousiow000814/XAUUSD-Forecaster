export const NEWS_EVIDENCE_CONTRACT_VERSION = "news-evidence-paged-v2";
export const NEWS_EVIDENCE_SNAPSHOT_ID = /^[a-f0-9]{64}$/;
export const NEWS_EVIDENCE_CURSOR_STALE = "NEWS_EVIDENCE_CURSOR_STALE";
export const NEWS_EVIDENCE_CURSOR_INVALID = "NEWS_EVIDENCE_CURSOR_INVALID";

export type EvidenceMode = "all" | "eligible" | "seen" | "unseen";

export type EvidenceItem = {
  event_key?: unknown;
  source_published_time?: unknown;
  collector_first_seen_time?: unknown;
  broad_model_eligible?: unknown;
  model_seen?: unknown;
  [key: string]: unknown;
};

export class NewsEvidenceProtocolError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    status: number,
    code: string,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function evidenceMode(value: string | null): EvidenceMode | null {
  return value === null || value === "all" ? "all"
    : value === "eligible" || value === "seen" || value === "unseen" ? value
      : null;
}

export function encodeEvidenceCursor(
  snapshotId: string, sortTime: string, eventKey: string,
): string {
  return JSON.stringify([snapshotId, sortTime, eventKey]);
}

export function decodeEvidenceCursor(raw: string): [string, string, string] {
  let cursor: unknown;
  try {
    cursor = JSON.parse(raw) as unknown;
  } catch {
    throw new NewsEvidenceProtocolError(
      "invalid evidence cursor", 400, NEWS_EVIDENCE_CURSOR_INVALID,
    );
  }
  if (
    !Array.isArray(cursor) || cursor.length !== 3
    || cursor.some(value => typeof value !== "string" || !value)
    || !NEWS_EVIDENCE_SNAPSHOT_ID.test(cursor[0] as string)
  ) {
    throw new NewsEvidenceProtocolError(
      "invalid evidence cursor", 400, NEWS_EVIDENCE_CURSOR_INVALID,
    );
  }
  return cursor as [string, string, string];
}

type ActiveState = {
  active_snapshot_id: string;
  contract_version: string;
  record_count: number;
  activated_at: string;
};

export async function readNewsEvidencePage(
  binding: D1Database,
  options: {
    mode: EvidenceMode;
    rawCursor: string | null;
    page: number;
    pageSize: number;
  },
) {
  const state = await binding.prepare(
    "SELECT active_snapshot_id,contract_version,record_count,activated_at "
    + "FROM news_evidence_publication WHERE id=1",
  ).first<ActiveState>();
  if (!state) {
    throw new NewsEvidenceProtocolError(
      "等待新闻证据首次同步", 503, "NEWS_EVIDENCE_NOT_SYNCHRONIZED",
    );
  }
  const conditions = ["(SELECT active_snapshot_id FROM news_evidence_publication WHERE id=1)=?"];
  const binds: Array<string | number> = [state.active_snapshot_id];
  if (options.mode === "eligible") conditions.push("broad_model_eligible=1");
  if (options.mode === "seen") conditions.push("model_seen=1");
  if (options.mode === "unseen") conditions.push("model_seen=0");
  if (options.rawCursor) {
    const [cursorSnapshot, sortTime, eventKey] = decodeEvidenceCursor(
      options.rawCursor,
    );
    if (cursorSnapshot !== state.active_snapshot_id) {
      throw new NewsEvidenceProtocolError(
        "evidence generation changed", 409, NEWS_EVIDENCE_CURSOR_STALE,
        { active_snapshot_id: state.active_snapshot_id },
      );
    }
    conditions.push("(sort_time<? OR (sort_time=? AND event_key<?))");
    binds.push(sortTime, sortTime, eventKey);
  }
  const boundedRows = await binding.prepare(
    `WITH page AS (SELECT payload,sort_time,event_key FROM news_evidence_current
     WHERE ${conditions.join(" AND ")}
     ORDER BY sort_time DESC,event_key DESC LIMIT ?)
     SELECT payload,sort_time,event_key,NULL AS snapshot_id FROM page
     UNION ALL SELECT NULL,NULL,NULL,active_snapshot_id FROM news_evidence_publication WHERE id=1
     ORDER BY snapshot_id,sort_time DESC,event_key DESC`,
  ).bind(...binds, options.pageSize + 1).all<{
    payload: string; sort_time: string; event_key: string; snapshot_id: string | null;
  }>();
  const observed = boundedRows.results.pop();
  if (observed?.snapshot_id !== state.active_snapshot_id) {
    throw new NewsEvidenceProtocolError("evidence generation changed", 409,
      NEWS_EVIDENCE_CURSOR_STALE, { active_snapshot_id: observed?.snapshot_id });
  }
  const hasMore = boundedRows.results.length > options.pageSize;
  const rows = boundedRows.results.slice(0, options.pageSize);
  const last = rows.at(-1);
  return {
    items: rows.map(row => JSON.parse(row.payload) as EvidenceItem),
    page: options.page,
    page_size: options.pageSize,
    mode: options.mode,
    has_more: hasMore,
    next_cursor: hasMore && last
      ? encodeEvidenceCursor(state.active_snapshot_id, last.sort_time, last.event_key)
      : null,
    snapshot_id: state.active_snapshot_id,
    contract_version: state.contract_version,
    activated_at: state.activated_at,
    source_mode: "D1_AUDIT_ARCHIVE",
  };
}

export function readPreviewNewsEvidencePage(
  snapshot: {
    snapshot_id: string;
    contract_version: string;
    activated_at?: string | null;
    items: Array<Record<string, unknown>>;
  },
  options: {
    mode: EvidenceMode;
    rawCursor: string | null;
    page: number;
    pageSize: number;
  },
) {
  if (!NEWS_EVIDENCE_SNAPSHOT_ID.test(snapshot.snapshot_id)) {
    throw new NewsEvidenceProtocolError(
      "invalid Preview evidence generation", 503,
      "NEWS_EVIDENCE_PREVIEW_INVALID",
    );
  }
  let cursor: [string, string, string] | null = null;
  if (options.rawCursor) {
    cursor = decodeEvidenceCursor(options.rawCursor);
    if (cursor[0] !== snapshot.snapshot_id) {
      throw new NewsEvidenceProtocolError(
        "evidence generation changed", 409, NEWS_EVIDENCE_CURSOR_STALE,
        { active_snapshot_id: snapshot.snapshot_id },
      );
    }
  }
  const rows = snapshot.items
    .filter(item => (
      options.mode === "eligible" ? item.broad_model_eligible === true
        : options.mode === "seen" ? item.model_seen === true
          : options.mode === "unseen" ? item.model_seen === false : true
    ))
    .map(item => ({
      item,
      sortTime: typeof item.source_published_time === "string"
        ? item.source_published_time
        : String(item.collector_first_seen_time ?? ""),
      eventKey: String(item.event_key ?? ""),
    }))
    .filter(row => row.sortTime && NEWS_EVIDENCE_SNAPSHOT_ID.test(row.eventKey))
    .sort((left, right) => (
      right.sortTime.localeCompare(left.sortTime)
      || right.eventKey.localeCompare(left.eventKey)
    ));
  const afterCursor = cursor
    ? rows.filter(row => (
      row.sortTime < cursor[1]
      || (row.sortTime === cursor[1] && row.eventKey < cursor[2])
    ))
    : rows;
  const pageRows = afterCursor.slice(0, options.pageSize + 1);
  const hasMore = pageRows.length > options.pageSize;
  const visible = pageRows.slice(0, options.pageSize);
  const last = visible.at(-1);
  return {
    items: visible.map(row => row.item),
    page: options.page,
    page_size: options.pageSize,
    mode: options.mode,
    has_more: hasMore,
    next_cursor: hasMore && last
      ? encodeEvidenceCursor(snapshot.snapshot_id, last.sortTime, last.eventKey)
      : null,
    snapshot_id: snapshot.snapshot_id,
    contract_version: snapshot.contract_version,
    activated_at: snapshot.activated_at ?? null,
    source_mode: "IMMUTABLE_BUILD_SNAPSHOT",
  };
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

/** Bind a completed write to the producer's exact bounded UTF-8 request. */
export async function newsEvidenceWriteAcknowledgement(
  serialized: string, snapshotId: string, result: Record<string, unknown>,
) {
  if (result.status !== "OK" || !NEWS_EVIDENCE_SNAPSHOT_ID.test(snapshotId)) {
    throw new NewsEvidenceProtocolError(
      "invalid evidence acknowledgement", 500, "NEWS_EVIDENCE_ACK_INVALID",
    );
  }
  return {
    ...result,
    contract_version: NEWS_EVIDENCE_CONTRACT_VERSION,
    snapshot_id: snapshotId,
    request_sha256: await sha256(serialized),
  };
}

export async function prepareNewsEvidenceBatch(
  items: EvidenceItem[], existingPayloadHash?: string,
) {
  const rows = items.map(item => {
    if (
      typeof item.event_key !== "string"
      || !NEWS_EVIDENCE_SNAPSHOT_ID.test(item.event_key)
      || typeof item.collector_first_seen_time !== "string"
      || typeof item.broad_model_eligible !== "boolean"
      || typeof item.model_seen !== "boolean"
    ) {
      throw new NewsEvidenceProtocolError(
        "invalid evidence item", 400, "NEWS_EVIDENCE_ITEM_INVALID",
      );
    }
    return {
      item,
      sortTime: typeof item.source_published_time === "string"
        ? item.source_published_time : item.collector_first_seen_time,
      serialized: JSON.stringify(item),
    };
  });
  const payloadHash = existingPayloadHash
    ?? await sha256(`[${rows.map(row => row.serialized).join(",")}]`);
  return { payloadHash, rows };
}

type Transfer = {
  base_snapshot_id: string; next_offset: number; expected_count: number;
};
const currentIdentity = "COALESCE((SELECT active_snapshot_id FROM news_evidence_publication WHERE id=1),'')";

export async function prepareNewsEvidenceSnapshot(
  binding: D1Database, snapshotId: string, expectedCount: number,
) {
  const active = await binding.prepare(
    "SELECT active_snapshot_id,record_count FROM news_evidence_publication WHERE id=1",
  ).first<{ active_snapshot_id: string; record_count: number }>();
  if (active?.active_snapshot_id === snapshotId && active.record_count === expectedCount) {
    return { status: "OK", active: true, next_offset: expectedCount };
  }
  const staging = await binding.prepare(
    "SELECT base_snapshot_id,next_offset,expected_count FROM news_evidence_transfers WHERE snapshot_id=?",
  ).bind(snapshotId).first<Transfer>();
  if (staging && staging.expected_count !== expectedCount) {
    throw new NewsEvidenceProtocolError("evidence generation manifest changed", 409,
      "NEWS_EVIDENCE_MANIFEST_MISMATCH");
  }
  let contiguous = 0;
  if (staging?.base_snapshot_id === (active?.active_snapshot_id ?? "")) {
    const receipts = await binding.prepare(
      `SELECT batch_offset,item_count,json_array_length(keys_json) AS record_count
       FROM news_evidence_receipts WHERE snapshot_id=? ORDER BY batch_offset`,
    ).bind(snapshotId).all<{ batch_offset: number; item_count: number; record_count: number }>();
    for (const receipt of receipts.results) {
      if (receipt.batch_offset !== contiguous || receipt.item_count < 1
          || receipt.record_count !== receipt.item_count) break;
      contiguous += receipt.item_count;
    }
  }
  if (!staging) {
    await binding.prepare(
      `INSERT INTO news_evidence_transfers
       (snapshot_id,base_snapshot_id,next_offset,expected_count,updated_at)
       VALUES (?,${currentIdentity},0,?,?)`,
    ).bind(snapshotId, expectedCount, new Date().toISOString()).run();
  } else if (contiguous !== staging.next_offset
      || staging.base_snapshot_id !== (active?.active_snapshot_id ?? "")) {
    await binding.batch([
      binding.prepare("DELETE FROM news_evidence_receipts WHERE snapshot_id=? AND batch_offset>=?")
        .bind(snapshotId, contiguous),
      binding.prepare(`UPDATE news_evidence_transfers SET next_offset=?,base_snapshot_id=?,updated_at=?
        WHERE snapshot_id=?`).bind(contiguous, active?.active_snapshot_id ?? "", new Date().toISOString(), snapshotId),
    ]);
  }
  return { status: "OK", active: false, next_offset: contiguous,
    ...(staging && contiguous !== staging.next_offset ? { repaired_from: staging.next_offset } : {}) };
}

export async function stageNewsEvidenceBatch(
  binding: D1Database, snapshotId: string, offset: number, items: EvidenceItem[],
) {
  const staging = await binding.prepare(
    "SELECT base_snapshot_id,next_offset,expected_count FROM news_evidence_transfers WHERE snapshot_id=?",
  ).bind(snapshotId).first<Transfer>();
  if (!staging) throw new NewsEvidenceProtocolError("evidence generation was not prepared", 409,
    "NEWS_EVIDENCE_NOT_PREPARED");
  if (offset + items.length > staging.expected_count || items.length === 0) {
    throw new NewsEvidenceProtocolError("evidence batch exceeds generation manifest", 409,
      "NEWS_EVIDENCE_BATCH_OVERFLOW");
  }
  const prepared = await prepareNewsEvidenceBatch(items);
  if (offset < staging.next_offset) {
    const receipt = await binding.prepare(
      "SELECT item_count,payload_hash FROM news_evidence_receipts WHERE snapshot_id=? AND batch_offset=?",
    ).bind(snapshotId, offset).first<{ item_count: number; payload_hash: string }>();
    if (receipt?.item_count === items.length && receipt.payload_hash === prepared.payloadHash
        && offset + items.length <= staging.next_offset) {
      return { status: "OK", received: items.length, duplicate: true };
    }
    throw new NewsEvidenceProtocolError("evidence batch replay does not match its receipt", 409,
      "NEWS_EVIDENCE_REPLAY_MISMATCH");
  }
  if (offset !== staging.next_offset) throw new NewsEvidenceProtocolError("evidence batch offset mismatch", 409,
    "NEWS_EVIDENCE_OFFSET_MISMATCH", { expected: staging.next_offset, received: offset });
  const serialized = JSON.stringify(items);
  const now = new Date().toISOString();
  await binding.batch([
    // CHECK(next_offset>=0) makes an invalidated comparison roll back the batch.
    binding.prepare(`UPDATE news_evidence_transfers SET next_offset=CASE
      WHEN next_offset=? AND base_snapshot_id=${currentIdentity} THEN ? ELSE -1 END,updated_at=?
      WHERE snapshot_id=?`).bind(offset, offset + items.length, now, snapshotId),
    binding.prepare(`WITH incoming AS (
        SELECT json_extract(value,'$.event_key') AS event_key,value AS payload FROM json_each(?)
      ) INSERT INTO news_evidence_receipts
        (snapshot_id,batch_offset,item_count,payload_hash,keys_json,changes_json,updated_at)
      SELECT ?,?,CASE WHEN EXISTS(SELECT 1 FROM news_evidence_transfers
        WHERE snapshot_id=? AND next_offset=? AND base_snapshot_id=${currentIdentity})
        THEN ? ELSE NULL END,?,
        (SELECT json_group_array(event_key) FROM incoming),
        (SELECT json_group_array(json(i.payload)) FROM incoming i
          LEFT JOIN news_evidence_current c ON c.event_key=i.event_key
          WHERE c.payload IS NOT i.payload),?`).bind(
      serialized, snapshotId, offset, snapshotId, offset + items.length,
      items.length, prepared.payloadHash, now),
  ]);
  return { status: "OK", received: items.length };
}

export async function activateNewsEvidenceSnapshot(
  binding: D1Database, snapshotId: string, expectedCount: number,
) {
  const active = await binding.prepare(
    "SELECT active_snapshot_id,record_count FROM news_evidence_publication WHERE id=1",
  ).first<{ active_snapshot_id: string; record_count: number }>();
  if (active?.active_snapshot_id === snapshotId && active.record_count === expectedCount) {
    return { status: "OK", activated: snapshotId, count: expectedCount, unchanged: true };
  }
  const staging = await binding.prepare(
    "SELECT base_snapshot_id,next_offset,expected_count FROM news_evidence_transfers WHERE snapshot_id=?",
  ).bind(snapshotId).first<Transfer>();
  const membership = `SELECT j.value FROM news_evidence_receipts r,json_each(r.keys_json) j
    WHERE r.snapshot_id=?`;
  const actual = await binding.prepare(
    `SELECT count(*) AS count,count(DISTINCT value) AS unique_count FROM (${membership})`,
  ).bind(snapshotId).first<{ count: number; unique_count: number }>();
  if (!staging || staging.expected_count !== expectedCount || staging.next_offset !== expectedCount
      || actual?.count !== expectedCount || actual.unique_count !== expectedCount
      || staging.base_snapshot_id !== (active?.active_snapshot_id ?? "")) {
    throw new NewsEvidenceProtocolError("incomplete evidence snapshot", 409, "NEWS_EVIDENCE_INCOMPLETE");
  }
  const now = new Date().toISOString();
  await binding.batch([
    binding.prepare(`UPDATE news_evidence_transfers SET next_offset=CASE
      WHEN base_snapshot_id=${currentIdentity} AND next_offset=? THEN next_offset ELSE -1 END
      WHERE snapshot_id=?`).bind(expectedCount, snapshotId),
    binding.prepare(`INSERT INTO news_evidence_current
      (event_key,sort_time,broad_model_eligible,model_seen,payload)
      SELECT json_extract(j.value,'$.event_key'),
        COALESCE(json_extract(j.value,'$.source_published_time'),json_extract(j.value,'$.collector_first_seen_time')),
        json_extract(j.value,'$.broad_model_eligible'),json_extract(j.value,'$.model_seen'),j.value
      FROM news_evidence_receipts r,json_each(r.changes_json) j WHERE r.snapshot_id=?
      ON CONFLICT(event_key) DO UPDATE SET sort_time=excluded.sort_time,
        broad_model_eligible=excluded.broad_model_eligible,model_seen=excluded.model_seen,payload=excluded.payload
      WHERE news_evidence_current.payload IS NOT excluded.payload`).bind(snapshotId),
    // Uncorrelated membership builds one key set, not a scan for each current row.
    binding.prepare(`DELETE FROM news_evidence_current WHERE event_key NOT IN (${membership})`).bind(snapshotId),
    binding.prepare(`INSERT INTO news_evidence_publication
      (id,active_snapshot_id,contract_version,record_count,activated_at)
      VALUES (1,?,?,CASE WHEN EXISTS(SELECT 1 FROM news_evidence_transfers
        WHERE snapshot_id=? AND base_snapshot_id=${currentIdentity} AND next_offset=? AND expected_count=?)
        AND (SELECT count(*) FROM news_evidence_current)=?
        THEN ? ELSE NULL END,?)
      ON CONFLICT(id) DO UPDATE SET active_snapshot_id=excluded.active_snapshot_id,
      contract_version=excluded.contract_version,record_count=excluded.record_count,activated_at=excluded.activated_at`)
      .bind(snapshotId, NEWS_EVIDENCE_CONTRACT_VERSION, snapshotId, expectedCount,
        expectedCount, expectedCount, expectedCount, now),
    binding.prepare("DELETE FROM news_evidence_transfers WHERE snapshot_id=?").bind(snapshotId),
  ]);
  return { status: "OK", activated: snapshotId, count: expectedCount };
}

export async function cleanupNewsEvidenceSnapshots(
  binding: D1Database, activeSnapshotId: string, now = new Date(),
) {
  const active = await binding.prepare("SELECT active_snapshot_id FROM news_evidence_publication WHERE id=1")
    .first<{ active_snapshot_id: string }>();
  if (!NEWS_EVIDENCE_SNAPSHOT_ID.test(activeSnapshotId) || active?.active_snapshot_id !== activeSnapshotId) {
    throw new NewsEvidenceProtocolError("invalid evidence cleanup", 409, "NEWS_EVIDENCE_CLEANUP_INVALID");
  }
  const readerCutoff = new Date(now.getTime() - 5 * 60_000).toISOString();
  const stagingCutoff = new Date(now.getTime() - 24 * 60 * 60_000).toISOString();
  const obsolete = `snapshot_id<>${currentIdentity} AND updated_at<? AND snapshot_id NOT IN
    (SELECT snapshot_id FROM news_evidence_transfers WHERE updated_at>=?)`;
  const results = await binding.batch([
    binding.prepare(`DELETE FROM news_evidence_receipts WHERE rowid IN
      (SELECT rowid FROM news_evidence_receipts WHERE ${obsolete} LIMIT 20)`)
      .bind(readerCutoff, stagingCutoff),
    binding.prepare(`DELETE FROM news_evidence_transfers WHERE snapshot_id IN
      (SELECT snapshot_id FROM news_evidence_transfers WHERE snapshot_id<>${currentIdentity}
      AND updated_at<? LIMIT 20)`).bind(stagingCutoff),
  ]);
  const pending = await binding.prepare(`SELECT EXISTS(SELECT 1 FROM news_evidence_receipts
    WHERE ${obsolete}) OR EXISTS(SELECT 1 FROM news_evidence_transfers
    WHERE snapshot_id<>${currentIdentity} AND updated_at<?) AS pending`)
    .bind(readerCutoff, stagingCutoff, stagingCutoff).first<{ pending: number }>();
  return { status: "OK", cleanup: "advanced", deleted_records: 0,
    deleted_batches: Number(results[0]?.meta?.changes ?? 0),
    deleted_staging: Number(results[1]?.meta?.changes ?? 0), cleanup_pending: pending?.pending === 1 };
}
