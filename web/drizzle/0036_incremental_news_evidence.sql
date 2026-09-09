CREATE TABLE news_evidence_current (
 event_key TEXT PRIMARY KEY NOT NULL,
 sort_time TEXT NOT NULL,
 broad_model_eligible INTEGER NOT NULL,
 model_seen INTEGER NOT NULL,
 payload TEXT NOT NULL
);
CREATE INDEX news_evidence_current_time ON news_evidence_current(sort_time,event_key);
CREATE INDEX news_evidence_current_eligible ON news_evidence_current(broad_model_eligible,sort_time,event_key);
CREATE INDEX news_evidence_current_seen ON news_evidence_current(model_seen,sort_time,event_key);
CREATE TABLE news_evidence_publication (
 id INTEGER PRIMARY KEY CHECK(id=1),
 active_snapshot_id TEXT NOT NULL,
 contract_version TEXT NOT NULL,
 record_count INTEGER NOT NULL,
 activated_at TEXT NOT NULL
);
CREATE TABLE news_evidence_transfers (
 snapshot_id TEXT PRIMARY KEY NOT NULL,
 base_snapshot_id TEXT NOT NULL,
 next_offset INTEGER NOT NULL CHECK(next_offset>=0),
 expected_count INTEGER NOT NULL CHECK(expected_count>=0),
 updated_at TEXT NOT NULL
);
CREATE TABLE news_evidence_receipts (
 snapshot_id TEXT NOT NULL,
 batch_offset INTEGER NOT NULL,
 item_count INTEGER NOT NULL,
 payload_hash TEXT NOT NULL,
 keys_json TEXT NOT NULL CHECK(json_valid(keys_json)),
 changes_json TEXT NOT NULL CHECK(json_valid(changes_json)),
 updated_at TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,batch_offset)
);
INSERT INTO news_evidence_current
 SELECT r.event_key,r.sort_time,r.broad_model_eligible,r.model_seen,r.payload
 FROM news_evidence_records r
 JOIN news_evidence_state s ON s.id=1 AND s.active_snapshot_id=r.snapshot_id;
INSERT INTO news_evidence_publication SELECT * FROM news_evidence_state WHERE id=1;
