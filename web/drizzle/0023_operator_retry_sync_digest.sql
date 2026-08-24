CREATE TABLE IF NOT EXISTS operator_retry_sync_state (
  id integer PRIMARY KEY NOT NULL CHECK (id = 1),
  payload_digest text NOT NULL,
  item_count integer NOT NULL,
  synced_at text NOT NULL
);
