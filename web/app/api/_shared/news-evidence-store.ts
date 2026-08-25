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
    + "FROM news_evidence_state WHERE id=1",
  ).first<ActiveState>();
  if (!state) {
    throw new NewsEvidenceProtocolError(
      "等待新闻证据首次同步", 503, "NEWS_EVIDENCE_NOT_SYNCHRONIZED",
    );
  }
  const conditions = ["snapshot_id=?"];
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
    `SELECT payload,sort_time,event_key FROM news_evidence_records
     WHERE ${conditions.join(" AND ")}
     ORDER BY sort_time DESC,event_key DESC LIMIT ?`,
  ).bind(...binds, options.pageSize + 1).all<{
    payload: string; sort_time: string; event_key: string;
  }>();
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

export async function prepareNewsEvidenceSnapshot(
  binding: D1Database, snapshotId: string, expectedCount: number,
) {
  const active = await binding.prepare(
    "SELECT active_snapshot_id,record_count FROM news_evidence_state WHERE id=1",
  ).first<{ active_snapshot_id: string; record_count: number }>();
  if (
    active?.active_snapshot_id === snapshotId
    && Number(active.record_count) === expectedCount
  ) {
    return { status: "OK", active: true, next_offset: expectedCount };
  }
  const staging = await binding.prepare(
    "SELECT next_offset,expected_count FROM news_evidence_staging WHERE snapshot_id=?",
  ).bind(snapshotId).first<{ next_offset: number; expected_count: number }>();
  if (staging && Number(staging.expected_count) !== expectedCount) {
    throw new NewsEvidenceProtocolError(
      "evidence generation manifest changed", 409,
      "NEWS_EVIDENCE_MANIFEST_MISMATCH",
    );
  }
  if (!staging) {
    await binding.prepare(
      `INSERT INTO news_evidence_staging
         (snapshot_id,next_offset,expected_count,updated_at) VALUES (?,0,?,?)`,
    ).bind(snapshotId, expectedCount, new Date().toISOString()).run();
  }
  return {
    status: "OK", active: false,
    next_offset: Number(staging?.next_offset ?? 0),
  };
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
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

export async function stageNewsEvidenceBatch(
  binding: D1Database,
  snapshotId: string,
  offset: number,
  items: EvidenceItem[],
) {
  const staging = await binding.prepare(
    "SELECT next_offset,expected_count FROM news_evidence_staging WHERE snapshot_id=?",
  ).bind(snapshotId).first<{ next_offset: number; expected_count: number }>();
  if (!staging) {
    throw new NewsEvidenceProtocolError(
      "evidence generation was not prepared", 409,
      "NEWS_EVIDENCE_NOT_PREPARED",
    );
  }
  const expectedCount = Number(staging.expected_count);
  const nextOffset = Number(staging.next_offset);
  if (offset + items.length > expectedCount) {
    throw new NewsEvidenceProtocolError(
      "evidence batch exceeds generation manifest", 409,
      "NEWS_EVIDENCE_BATCH_OVERFLOW",
    );
  }
  const payloadHash = await sha256(JSON.stringify(items));
  if (offset < nextOffset) {
    const receipt = await binding.prepare(
      `SELECT item_count,payload_hash FROM news_evidence_batches
       WHERE snapshot_id=? AND batch_offset=?`,
    ).bind(snapshotId, offset).first<{ item_count: number; payload_hash: string }>();
    if (
      receipt && Number(receipt.item_count) === items.length
      && receipt.payload_hash === payloadHash
      && offset + items.length <= nextOffset
    ) {
      return { status: "OK", received: items.length, duplicate: true };
    }
    throw new NewsEvidenceProtocolError(
      "evidence batch replay does not match its receipt", 409,
      "NEWS_EVIDENCE_REPLAY_MISMATCH",
    );
  }
  if (offset !== nextOffset) {
    throw new NewsEvidenceProtocolError(
      "evidence batch offset mismatch", 409,
      "NEWS_EVIDENCE_OFFSET_MISMATCH",
      { expected: nextOffset, received: offset },
    );
  }
  const prepared = await prepareNewsEvidenceBatch(items, payloadHash);
  const now = new Date().toISOString();
  const statements = prepared.rows.map((row, index) => {
    const item = row.item;
    return binding.prepare(
      `INSERT INTO news_evidence_records
         (snapshot_id,event_key,ordinal,sort_time,broad_model_eligible,model_seen,payload,received_at)
       VALUES (?,?,?,?,?,?,?,?)`,
    ).bind(
      snapshotId, item.event_key, offset + index, row.sortTime,
      item.broad_model_eligible ? 1 : 0, item.model_seen ? 1 : 0,
      row.serialized, now,
    );
  });
  statements.push(binding.prepare(
    `INSERT INTO news_evidence_batches
       (snapshot_id,batch_offset,item_count,payload_hash,updated_at)
     VALUES (?,?,?,?,?)`,
  ).bind(snapshotId, offset, items.length, payloadHash, now));
  statements.push(binding.prepare(
    `UPDATE news_evidence_staging SET next_offset=?,updated_at=?
     WHERE snapshot_id=? AND next_offset=?`,
  ).bind(offset + items.length, now, snapshotId, offset));
  await binding.batch(statements);
  return { status: "OK", received: items.length };
}

export async function activateNewsEvidenceSnapshot(
  binding: D1Database, snapshotId: string, expectedCount: number,
) {
  const active = await binding.prepare(
    "SELECT active_snapshot_id,record_count FROM news_evidence_state WHERE id=1",
  ).first<{ active_snapshot_id: string; record_count: number }>();
  if (
    active?.active_snapshot_id === snapshotId
    && Number(active.record_count) === expectedCount
  ) {
    return {
      status: "OK", activated: snapshotId, count: expectedCount, unchanged: true,
    };
  }
  const staging = await binding.prepare(
    "SELECT next_offset,expected_count FROM news_evidence_staging WHERE snapshot_id=?",
  ).bind(snapshotId).first<{ next_offset: number; expected_count: number }>();
  const actual = await binding.prepare(
    "SELECT count(*) AS count FROM news_evidence_records WHERE snapshot_id=?",
  ).bind(snapshotId).first<{ count: number }>();
  if (
    !staging || Number(staging.expected_count) !== expectedCount
    || Number(staging.next_offset) !== expectedCount
    || Number(actual?.count ?? -1) !== expectedCount
  ) {
    throw new NewsEvidenceProtocolError(
      "incomplete evidence snapshot", 409, "NEWS_EVIDENCE_INCOMPLETE",
      {
        expected: expectedCount,
        received: Number(staging?.next_offset ?? -1),
        stored: Number(actual?.count ?? -1),
      },
    );
  }
  const now = new Date().toISOString();
  await binding.batch([
    binding.prepare(
      `INSERT INTO news_evidence_state
         (id,active_snapshot_id,contract_version,record_count,activated_at)
       VALUES (1,?,?,?,?)
       ON CONFLICT(id) DO UPDATE SET
         active_snapshot_id=excluded.active_snapshot_id,
         contract_version=excluded.contract_version,
         record_count=excluded.record_count,
         activated_at=excluded.activated_at`,
    ).bind(snapshotId, NEWS_EVIDENCE_CONTRACT_VERSION, expectedCount, now),
    binding.prepare(
      "DELETE FROM news_evidence_staging WHERE snapshot_id=?",
    ).bind(snapshotId),
  ]);
  return { status: "OK", activated: snapshotId, count: expectedCount };
}

export async function cleanupNewsEvidenceSnapshots(
  binding: D1Database, activeSnapshotId: string,
) {
  const active = await binding.prepare(
    "SELECT active_snapshot_id FROM news_evidence_state WHERE id=1",
  ).first<{ active_snapshot_id: string }>();
  if (
    !NEWS_EVIDENCE_SNAPSHOT_ID.test(activeSnapshotId)
    || active?.active_snapshot_id !== activeSnapshotId
  ) {
    throw new NewsEvidenceProtocolError(
      "invalid evidence cleanup", 409, "NEWS_EVIDENCE_CLEANUP_INVALID",
    );
  }
  const readerCutoff = new Date(Date.now() - 5 * 60_000).toISOString();
  const stagingCutoff = new Date(Date.now() - 24 * 60 * 60_000).toISOString();
  const cleanup = await binding.batch([
    binding.prepare(
      `DELETE FROM news_evidence_records WHERE rowid IN (
         SELECT rowid FROM news_evidence_records
         WHERE snapshot_id<>? AND received_at<? LIMIT 200
       )`,
    ).bind(activeSnapshotId, readerCutoff),
    binding.prepare(
      `DELETE FROM news_evidence_batches WHERE rowid IN (
         SELECT rowid FROM news_evidence_batches
         WHERE snapshot_id<>? AND updated_at<? LIMIT 20
       )`,
    ).bind(activeSnapshotId, readerCutoff),
    binding.prepare(
      `DELETE FROM news_evidence_staging WHERE snapshot_id IN (
         SELECT snapshot_id FROM news_evidence_staging
         WHERE snapshot_id<>? AND updated_at<? LIMIT 20
       )`,
    ).bind(activeSnapshotId, stagingCutoff),
  ]);
  const pending = await binding.prepare(
    `SELECT (
       EXISTS(SELECT 1 FROM news_evidence_records
         WHERE snapshot_id<>? AND received_at<? LIMIT 1)
       OR EXISTS(SELECT 1 FROM news_evidence_batches
         WHERE snapshot_id<>? AND updated_at<? LIMIT 1)
       OR EXISTS(SELECT 1 FROM news_evidence_staging
         WHERE snapshot_id<>? AND updated_at<? LIMIT 1)
     ) AS cleanup_pending`,
  ).bind(
    activeSnapshotId, readerCutoff, activeSnapshotId, readerCutoff,
    activeSnapshotId, stagingCutoff,
  )
    .first<{ cleanup_pending: number }>();
  return {
    status: "OK", cleanup: "advanced",
    deleted_records: Number(cleanup[0]?.meta?.changes ?? 0),
    deleted_batches: Number(cleanup[1]?.meta?.changes ?? 0),
    deleted_staging: Number(cleanup[2]?.meta?.changes ?? 0),
    cleanup_pending: Number(pending?.cleanup_pending ?? 0) === 1,
  };
}
