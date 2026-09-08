CREATE INDEX IF NOT EXISTS news_index_superseded_cleanup_idx
ON news_index (detail_key)
WHERE COALESCE(json_extract(payload,'$.annotation_status'),'')='SUPERSEDED_CONTRACT';
