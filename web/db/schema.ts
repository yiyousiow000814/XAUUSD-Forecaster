import { index, integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

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
