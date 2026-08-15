import { index, integer, primaryKey, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const dashboardSnapshots = sqliteTable("dashboard_snapshots", {
  id: integer("id").primaryKey(),
  payload: text("payload").notNull(),
  receivedAt: text("received_at").notNull(),
});

export const newsDetails = sqliteTable("news_details", {
  detailKey: text("detail_key").primaryKey(),
  detailHash: text("detail_hash").notNull(),
  payload: text("payload").notNull(),
  receivedAt: text("received_at").notNull(),
});

export const newsIndex = sqliteTable(
  "news_index",
  {
    detailKey: text("detail_key").primaryKey(),
    category: text("category").notNull(),
    clusterId: text("cluster_id").notNull(),
    publishedTime: text("published_time").notNull(),
    collectorFirstSeenTime: text("collector_first_seen_time").notNull(),
    parsed: integer("parsed").notNull().default(0),
    modelCandidate: integer("model_candidate").notNull().default(0),
    impactExpiresAt: text("impact_expires_at"),
    mirrorContract: text("mirror_contract").notNull().default(""),
    payload: text("payload").notNull(),
    receivedAt: text("received_at").notNull(),
  },
  table => [
    index("news_index_seen_idx").on(table.collectorFirstSeenTime),
    index("news_index_published_idx").on(table.publishedTime),
    index("news_index_cluster_idx").on(table.clusterId),
    index("news_index_category_published_idx").on(
      table.category, table.publishedTime,
    ),
  ],
);

export const marketCandles = sqliteTable("market_candles", {
  timeEpoch: integer("time_epoch").primaryKey(),
  time: text("time").notNull(),
  open: integer("open_milli").notNull(),
  high: integer("high_milli").notNull(),
  low: integer("low_milli").notNull(),
  close: integer("close_milli").notNull(),
  ticks: integer("ticks").notNull(),
  receivedAt: text("received_at").notNull(),
});

export const marketDecisions = sqliteTable(
  "market_decisions",
  {
    decisionKey: text("decision_key").primaryKey(),
    decisionEpoch: integer("decision_epoch").notNull(),
    decisionTime: text("decision_time").notNull(),
    modelIdentity: text("model_identity").notNull(),
    payload: text("payload").notNull(),
    receivedAt: text("received_at").notNull(),
  },
  table => [
    index("market_decisions_time_idx").on(table.decisionEpoch),
    index("market_decisions_model_time_idx").on(
      table.modelIdentity, table.decisionEpoch,
    ),
  ],
);

export const newsQuestions = sqliteTable(
  "news_questions",
  {
    id: text("id").primaryKey(),
    ownerId: text("owner_id").notNull(),
    idempotencyKey: text("idempotency_key").notNull(),
    questionHash: text("question_hash").notNull(),
    question: text("question").notNull(),
    retrievalQuery: text("retrieval_query").notNull(),
    status: text("status").notNull(),
    askedAt: text("asked_at").notNull(),
    availableAt: text("available_at").notNull(),
    expiresAt: text("expires_at").notNull(),
    processingStartedAt: text("processing_started_at"),
    leaseOwner: text("lease_owner"),
    leaseToken: text("lease_token"),
    leaseExpiresAt: text("lease_expires_at"),
    attemptCount: integer("attempt_count").notNull().default(0),
    maxAttempts: integer("max_attempts").notNull().default(3),
    attemptHistoryJson: text("attempt_history_json").notNull().default("[]"),
    failureCode: text("failure_code"),
    answer: text("answer"),
    answerStatus: text("answer_status"),
    evidenceJson: text("evidence_json"),
    retrievalJson: text("retrieval_json"),
    answeredAt: text("answered_at"),
    modelVersion: text("model_version"),
    promptVersion: text("prompt_version").notNull(),
    conversationId: text("conversation_id")
      .references(() => assistantConversations.id),
    userMessageId: text("user_message_id")
      .references(() => assistantMessages.id),
    assistantMessageId: text("assistant_message_id")
      .references(() => assistantMessages.id),
    evidenceValidationJson: text("evidence_validation_json"),
  },
  table => [
    uniqueIndex("news_questions_owner_idempotency_idx")
      .on(table.ownerId, table.idempotencyKey),
    uniqueIndex("news_questions_owner_hash_idx")
      .on(table.ownerId, table.questionHash),
    index("news_questions_claim_idx")
      .on(table.status, table.availableAt, table.askedAt),
    index("news_questions_owner_time_idx")
      .on(table.ownerId, table.askedAt),
    index("news_questions_lease_idx")
      .on(table.status, table.leaseExpiresAt),
    index("news_questions_conversation_idx").on(table.conversationId),
  ],
);

export const assistantConversations = sqliteTable(
  "assistant_conversations",
  {
    id: text("id").primaryKey(),
    ownerId: text("owner_id").notNull(),
    initialIdempotencyKey: text("initial_idempotency_key").notNull(),
    title: text("title").notNull(),
    titleSource: text("title_source").notNull(),
    titleRevision: integer("title_revision").notNull().default(0),
    titleRequestVersion: integer("title_request_version").notNull().default(0),
    pendingTitleJobId: text("pending_title_job_id"),
    createdAt: text("created_at").notNull(),
    lastActivityAt: text("last_activity_at").notNull(),
    archivedAt: text("archived_at"),
    summaryVersion: integer("summary_version").notNull().default(0),
    pendingCompactionJobId: text("pending_compaction_job_id"),
    compactionRequestVersion: integer("compaction_request_version").notNull().default(0),
    status: text("status").notNull().default("ACTIVE"),
  },
  table => [
    uniqueIndex("assistant_conversations_owner_idempotency_idx")
      .on(table.ownerId, table.initialIdempotencyKey),
    index("assistant_conversations_owner_activity_idx")
      .on(table.ownerId, table.status, table.lastActivityAt, table.id),
  ],
);

export const assistantMessages = sqliteTable(
  "assistant_messages",
  {
    id: text("id").primaryKey(),
    conversationId: text("conversation_id").notNull()
      .references(() => assistantConversations.id),
    role: text("role").notNull(),
    content: text("content").notNull(),
    createdAt: text("created_at").notNull(),
    provenanceJson: text("provenance_json").notNull(),
    sourceKind: text("source_kind").notNull(),
    sourceId: text("source_id").notNull(),
  },
  table => [
    uniqueIndex("assistant_messages_source_idx")
      .on(table.sourceKind, table.sourceId, table.role),
    index("assistant_messages_conversation_time_idx")
      .on(table.conversationId, table.createdAt, table.id),
  ],
);

export const assistantTitleJobs = sqliteTable(
  "assistant_title_jobs",
  {
    id: text("id").primaryKey(),
    conversationId: text("conversation_id").notNull()
      .references(() => assistantConversations.id),
    idempotencyKey: text("idempotency_key").notNull(),
    requestedBy: text("requested_by").notNull(),
    inputVersion: integer("input_version").notNull(),
    expectedTitleRevision: integer("expected_title_revision").notNull(),
    firstUserMessageId: text("first_user_message_id").notNull()
      .references(() => assistantMessages.id),
    assistantMessageId: text("assistant_message_id").notNull()
      .references(() => assistantMessages.id),
    status: text("status").notNull(),
    availableAt: text("available_at").notNull(),
    leaseOwner: text("lease_owner"),
    leaseToken: text("lease_token"),
    leaseExpiresAt: text("lease_expires_at"),
    attemptCount: integer("attempt_count").notNull().default(0),
    maxAttempts: integer("max_attempts").notNull().default(3),
    attemptHistoryJson: text("attempt_history_json").notNull().default("[]"),
    promptVersion: text("prompt_version").notNull(),
    modelVersion: text("model_version"),
    createdAt: text("created_at").notNull(),
    completedAt: text("completed_at"),
    generatedTitle: text("generated_title"),
    failureCode: text("failure_code"),
  },
  table => [
    uniqueIndex("assistant_title_jobs_version_idx")
      .on(table.conversationId, table.inputVersion),
    uniqueIndex("assistant_title_jobs_idempotency_idx")
      .on(table.conversationId, table.idempotencyKey),
    index("assistant_title_jobs_claim_idx")
      .on(table.status, table.availableAt, table.createdAt),
    index("assistant_title_jobs_lease_idx")
      .on(table.status, table.leaseExpiresAt),
  ],
);

export const assistantCompactionJobs = sqliteTable(
  "assistant_compaction_jobs",
  {
    id: text("id").primaryKey(),
    conversationId: text("conversation_id").notNull()
      .references(() => assistantConversations.id),
    inputVersion: integer("input_version").notNull(),
    priorSummaryVersion: integer("prior_summary_version").notNull(),
    outputSummaryVersion: integer("output_summary_version").notNull(),
    sourceMessageIdsJson: text("source_message_ids_json").notNull(),
    sourceMessageCount: integer("source_message_count").notNull(),
    firstSourceMessageId: text("first_source_message_id").notNull()
      .references(() => assistantMessages.id),
    lastSourceMessageId: text("last_source_message_id").notNull()
      .references(() => assistantMessages.id),
    pinnedSnapshotJson: text("pinned_snapshot_json").notNull(),
    contextProfileId: text("context_profile_id").notNull(),
    capacityState: text("capacity_state").notNull(),
    estimatedContextTokens: integer("estimated_context_tokens").notNull(),
    status: text("status").notNull(),
    availableAt: text("available_at").notNull(),
    leaseOwner: text("lease_owner"),
    leaseToken: text("lease_token"),
    leaseExpiresAt: text("lease_expires_at"),
    attemptCount: integer("attempt_count").notNull().default(0),
    maxAttempts: integer("max_attempts").notNull().default(3),
    attemptHistoryJson: text("attempt_history_json").notNull().default("[]"),
    promptVersion: text("prompt_version").notNull(),
    modelVersion: text("model_version"),
    createdAt: text("created_at").notNull(),
    completedAt: text("completed_at"),
    failureCode: text("failure_code"),
  },
  table => [
    uniqueIndex("assistant_compaction_jobs_version_idx")
      .on(table.conversationId, table.inputVersion),
    index("assistant_compaction_jobs_output_idx")
      .on(table.conversationId, table.outputSummaryVersion, table.status),
    index("assistant_compaction_jobs_claim_idx")
      .on(table.status, table.availableAt, table.createdAt),
    index("assistant_compaction_jobs_lease_idx")
      .on(table.status, table.leaseExpiresAt),
  ],
);

export const assistantPinnedEntries = sqliteTable(
  "assistant_pinned_entries",
  {
    id: text("id").primaryKey(),
    conversationId: text("conversation_id").notNull()
      .references(() => assistantConversations.id),
    idempotencyKey: text("idempotency_key").notNull(),
    kind: text("kind").notNull(),
    content: text("content").notNull(),
    originMessageIdsJson: text("origin_message_ids_json").notNull(),
    evidenceIdsJson: text("evidence_ids_json").notNull(),
    sourceRefsJson: text("source_refs_json").notNull(),
    importantTimestampsJson: text("important_timestamps_json").notNull(),
    toolRefsJson: text("tool_refs_json").notNull(),
    artifactRefsJson: text("artifact_refs_json").notNull(),
    createdBy: text("created_by").notNull(),
    sourceJobId: text("source_job_id")
      .references(() => assistantCompactionJobs.id),
    createdAt: text("created_at").notNull(),
  },
  table => [
    uniqueIndex("assistant_pinned_entries_idempotency_idx")
      .on(table.conversationId, table.idempotencyKey),
    index("assistant_pinned_entries_conversation_idx")
      .on(table.conversationId, table.createdAt, table.id),
  ],
);

export const assistantSummaries = sqliteTable(
  "assistant_summaries",
  {
    id: text("id").primaryKey(),
    conversationId: text("conversation_id").notNull()
      .references(() => assistantConversations.id),
    version: integer("version").notNull(),
    priorSummaryId: text("prior_summary_id"),
    sourceJobId: text("source_job_id").notNull()
      .references(() => assistantCompactionJobs.id),
    firstSourceMessageId: text("first_source_message_id").notNull()
      .references(() => assistantMessages.id),
    coveredThroughMessageId: text("covered_through_message_id").notNull()
      .references(() => assistantMessages.id),
    coveredThroughCreatedAt: text("covered_through_created_at").notNull(),
    sourceMessageCount: integer("source_message_count").notNull(),
    content: text("content").notNull(),
    anchorsJson: text("anchors_json").notNull(),
    estimatedTokens: integer("estimated_tokens").notNull(),
    contextProfileId: text("context_profile_id").notNull(),
    promptVersion: text("prompt_version").notNull(),
    modelVersion: text("model_version").notNull(),
    createdAt: text("created_at").notNull(),
  },
  table => [
    uniqueIndex("assistant_summaries_version_idx")
      .on(table.conversationId, table.version),
    uniqueIndex("assistant_summaries_source_job_idx").on(table.sourceJobId),
    index("assistant_summaries_conversation_idx")
      .on(table.conversationId, table.version),
  ],
);

export const marketHistoryOverview = sqliteTable("market_history_overview", {
  overviewKey: text("overview_key").primaryKey(),
  payload: text("payload").notNull(),
  receivedAt: text("received_at").notNull(),
});

export const marketDecisionOverviews = sqliteTable(
  "market_decision_overviews",
  {
    overviewKey: text("overview_key").primaryKey(),
    modelIdentity: text("model_identity").notNull(),
    frequency: text("frequency").notNull(),
    payload: text("payload").notNull(),
    receivedAt: text("received_at").notNull(),
  },
);

export const learningRecords = sqliteTable(
  "learning_records",
  {
    resource: text("resource").notNull(),
    recordKey: text("record_key").notNull(),
    sortEpoch: integer("sort_epoch").notNull(),
    payloadHash: text("payload_hash").notNull(),
    payload: text("payload").notNull(),
    receivedAt: text("received_at").notNull(),
  },
  table => [
    primaryKey({ columns: [table.resource, table.recordKey] }),
    index("learning_records_resource_time_idx").on(
      table.resource, table.sortEpoch, table.recordKey,
    ),
  ],
);
