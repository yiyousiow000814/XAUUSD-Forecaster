-- Completion is written only after every exact chart export page is acknowledged.
CREATE TABLE IF NOT EXISTS chart_history_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  payload TEXT NOT NULL
);
