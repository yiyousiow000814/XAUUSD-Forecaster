-- Sparse current publications use the same ownership fences as full replay.
CREATE TRIGGER IF NOT EXISTS news_delta_current_index_delete_fence
BEFORE DELETE ON news_index
WHEN COALESCE(json_extract(OLD.payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT'
 AND EXISTS (SELECT 1 FROM news_projection_state
   WHERE id=1 AND projection_state='CURRENT' AND contract_version='news-projection-delta-v1')
BEGIN
  SELECT RAISE(IGNORE);
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS news_delta_current_detail_delete_fence
BEFORE DELETE ON news_details
WHEN EXISTS (SELECT 1 FROM news_projection_state s JOIN news_index i ON i.detail_key=OLD.detail_key
 WHERE s.id=1 AND s.projection_state='CURRENT' AND s.contract_version='news-projection-delta-v1'
 AND COALESCE(json_extract(i.payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT')
BEGIN
  SELECT RAISE(IGNORE);
END;
