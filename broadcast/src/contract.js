export const LIVE_SCHEMA_VERSION = "PUBLIC_LIVE_V1";
export const MAX_LIVE_BYTES = 16_384;
export const MAX_RECENT_DECISIONS = 6;

const PRIVATE_KEYS = new Set([
  "gemini_quota", "gemini_31_quota", "gemma_quota", "gemini_embedding_quota",
  "annotation_queue", "llm_routing", "admin", "features", "tokens", "secrets",
  "learning_history", "market_history", "news_archive",
]);

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function object(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function containsPrivateKey(value) {
  if (Array.isArray(value)) return value.some(containsPrivateKey);
  if (!object(value)) return false;
  return Object.entries(value).some(([key, nested]) => (
    PRIVATE_KEYS.has(key.toLowerCase()) || containsPrivateKey(nested)
  ));
}

export function serializedBytes(value) {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

export function validateLiveState(value) {
  if (!object(value)) throw new TypeError("live state must be an object");
  if (value.schema_version !== LIVE_SCHEMA_VERSION) throw new TypeError("invalid schema_version");
  if (!Number.isSafeInteger(value.sequence) || value.sequence < 1) throw new TypeError("invalid sequence");
  for (const key of ["generated_at", "source_revision", "market_session"]) {
    if (typeof value[key] !== "string" || !value[key]) throw new TypeError(`invalid ${key}`);
  }
  if (!object(value.freshness) || typeof value.freshness.online !== "boolean") {
    throw new TypeError("invalid freshness");
  }
  if (!object(value.quote)
      || !finiteNumber(value.quote.bid)
      || !finiteNumber(value.quote.ask)
      || !finiteNumber(value.quote.spread)
      || typeof value.quote.source_received_time !== "string") {
    throw new TypeError("invalid quote");
  }
  if (value.quote.spread < 0) throw new TypeError("invalid quote spread");
  if (value.quote.ask < value.quote.bid) throw new TypeError("crossed quote");
  if (!object(value.forecast) || !object(value.health)) throw new TypeError("invalid summaries");
  if (value.recent_decisions !== undefined) {
    if (!Array.isArray(value.recent_decisions)
        || value.recent_decisions.length > MAX_RECENT_DECISIONS) {
      throw new TypeError("recent_decisions is not bounded");
    }
  }
  if (containsPrivateKey(value)) throw new TypeError("private field is forbidden");
  if (serializedBytes(value) > MAX_LIVE_BYTES) throw new TypeError("live state is oversized");
  return value;
}

export function stateUpdate(previous, next) {
  const update = { ...next };
  if (JSON.stringify(previous?.recent_decisions ?? []) === JSON.stringify(next.recent_decisions ?? [])) {
    delete update.recent_decisions;
  }
  return update;
}
