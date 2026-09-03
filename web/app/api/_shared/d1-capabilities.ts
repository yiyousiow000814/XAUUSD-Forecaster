export const D1_CAPABILITIES = {
  operator_retry_scheduling: [
    "operator_retry_jobs",
    "operator_retry_requests",
    "operator_retry_request_events",
    "operator_retry_sync_state",
  ],
  paged_news_evidence: [
    "news_evidence_records",
    "news_evidence_state",
    "news_evidence_staging",
    "news_evidence_batches",
    "news_evidence_cleanup_budget",
  ],
  news_projection_generation: [
    "news_projection_generations",
    "news_projection_index",
    "news_projection_details",
    "news_projection_batches",
    "news_projection_receipts_v2",
    "news_projection_state",
    "news_projection_counts",
  ],
} as const;

export type D1Capability = keyof typeof D1_CAPABILITIES;

export class D1CapabilityError extends Error {
  readonly code = "D1_SCHEMA_CAPABILITY_MISSING";
  readonly missingCapabilities: D1Capability[];
  readonly missingTables: string[];

  constructor(missingCapabilities: D1Capability[], missingTables: string[]) {
    super("production D1 is missing required schema capabilities");
    this.missingCapabilities = missingCapabilities;
    this.missingTables = missingTables;
  }
}

// A capability set is immutable for the lifetime of one deployed Worker
// version. Cache only successful observations inside the isolate; a failed
// observation is removed immediately so an additive migration can recover
// without waiting for the isolate to be recycled.
const capabilityObservations = new WeakMap<object, Map<string, Promise<void>>>();

async function observeD1Capabilities(
  binding: D1Database,
  capabilities: readonly D1Capability[],
) {
  const requiredTables = [...new Set(
    capabilities.flatMap(capability => [...D1_CAPABILITIES[capability]]),
  )];
  const placeholders = requiredTables.map(() => "?").join(",");
  const rows = await binding.prepare(
    `SELECT name FROM sqlite_schema WHERE type='table' AND name IN (${placeholders})`,
  ).bind(...requiredTables).all<{ name: string }>();
  const present = new Set(rows.results.map(row => row.name));
  const missingTables = requiredTables.filter(table => !present.has(table));
  const missingCapabilities = capabilities.filter(capability =>
    D1_CAPABILITIES[capability].some(table => !present.has(table)),
  );
  if (missingCapabilities.length) {
    throw new D1CapabilityError(missingCapabilities, missingTables);
  }
}

export async function requireD1Capabilities(
  binding: D1Database,
  capabilities: readonly D1Capability[],
) {
  const owner = binding as unknown as object;
  const key = [...capabilities].sort().join("\u0000");
  let observations = capabilityObservations.get(owner);
  if (!observations) {
    observations = new Map();
    capabilityObservations.set(owner, observations);
  }
  let observation = observations.get(key);
  if (!observation) {
    observation = observeD1Capabilities(binding, capabilities);
    observations.set(key, observation);
  }
  try {
    await observation;
  } catch (error) {
    if (observations.get(key) === observation) observations.delete(key);
    throw error;
  }
}

export function d1CapabilityFailure(error: D1CapabilityError) {
  return {
    status: "ERROR",
    error: "production storage schema is not ready",
    error_code: error.code,
    missing_capabilities: error.missingCapabilities,
    missing_tables: error.missingTables,
  };
}
