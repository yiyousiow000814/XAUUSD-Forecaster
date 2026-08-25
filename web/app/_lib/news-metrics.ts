export type NewsMetrics = {
  schema_version: "news-metrics-v1";
  articles: {
    received: number;
    stored_revisions: number;
    readable: number;
    semantic_reviews_complete: number;
    current_model_candidates: number;
  };
  events: {
    independent: number;
    auditable: number;
    currently_model_eligible: number;
    used_in_predictions: number;
    never_used: number;
  };
  prediction_usage: {
    decision_event_exposures: number;
    frozen_model_uses: number;
  };
  training: {
    current_contract_rows: number;
    distinct_events: number;
  };
};

type LegacyNewsPayload = {
  news_metrics?: NewsMetrics;
  counts?: Record<string, number>;
  news_evidence_summary?: Partial<{
    raw_article_revisions: number;
    distinct_articles: number;
    total_events: number;
    displayed_events: number;
    broad_model_eligible: number;
    model_seen_events: number;
    model_unseen_events: number;
    decision_event_exposures: number;
    frozen_model_uses: number;
    current_contract_exposed_rows: number;
    current_contract_distinct_events: number;
  }>;
};

/** One compatibility boundary; views never reinterpret news counts themselves. */
export function resolveNewsMetrics(payload?: LegacyNewsPayload | null): NewsMetrics {
  if (payload?.news_metrics) return payload.news_metrics;
  const counts = payload?.counts ?? {};
  const evidence = payload?.news_evidence_summary ?? {};
  const unknown = Number.NaN;
  return {
    schema_version: "news-metrics-v1",
    articles: {
      received: evidence.distinct_articles ?? unknown,
      stored_revisions: evidence.raw_article_revisions ?? counts.news_revisions ?? unknown,
      readable: counts.readable_news_items ?? unknown,
      semantic_reviews_complete: counts.parsed_news_items ?? unknown,
      current_model_candidates: counts.model_candidate_news_items ?? unknown,
    },
    events: {
      independent: evidence.total_events ?? unknown,
      auditable: evidence.displayed_events ?? unknown,
      currently_model_eligible: evidence.broad_model_eligible ?? unknown,
      used_in_predictions: evidence.model_seen_events ?? unknown,
      never_used: evidence.model_unseen_events ?? unknown,
    },
    prediction_usage: {
      decision_event_exposures: evidence.decision_event_exposures ?? unknown,
      frozen_model_uses: evidence.frozen_model_uses ?? unknown,
    },
    training: {
      current_contract_rows: evidence.current_contract_exposed_rows ?? unknown,
      distinct_events: evidence.current_contract_distinct_events ?? unknown,
    },
  };
}
