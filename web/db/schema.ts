import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

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
    collectorFirstSeenTime: text("collector_first_seen_time").notNull(),
    payload: text("payload").notNull(),
    receivedAt: text("received_at").notNull(),
  },
  table => [
    index("news_index_seen_idx").on(table.collectorFirstSeenTime),
    index("news_index_category_seen_idx").on(
      table.category, table.collectorFirstSeenTime,
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
    id: text("id").primaryKey(), questionHash: text("question_hash").notNull().unique(),
    question: text("question").notNull(), status: text("status").notNull(),
    askedAt: text("asked_at").notNull(), answer: text("answer"),
    evidenceJson: text("evidence_json"), answeredAt: text("answered_at"),
    modelVersion: text("model_version"),
  },
  table => [index("news_questions_status_time_idx").on(table.status, table.askedAt)],
);
