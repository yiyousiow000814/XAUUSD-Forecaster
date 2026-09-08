WITH current_projection AS MATERIALIZED (
 SELECT json_extract(j.value,'$.detail_key') AS detail_key,
        json_extract(j.value,'$.category') AS category,
        json_extract(j.value,'$.cluster_id') AS cluster_id,
        coalesce(json_extract(j.value,'$.source_published_time'),
                 json_extract(j.value,'$.collector_first_seen_time')) AS published_time,
        json_extract(j.value,'$.collector_first_seen_time') AS collector_first_seen_time,
        CASE WHEN json_type(j.value,'$.parsed_at')='text' THEN 1 ELSE 0 END AS parsed,
        CASE WHEN json_extract(j.value,'$.model_visibility')='MODEL_VISIBLE'
             THEN 1 ELSE 0 END AS model_candidate,
        json_extract(j.value,'$.impact_expires_at') AS impact_expires_at,
        json_extract(j.value,'$.mirror_contract') AS mirror_contract,
        j.value AS payload
   FROM news_projection_receipts_v2 r,json_each(r.items_json) j
   JOIN news_projection_state active
     ON active.id=1 AND active.active_generation_id=r.generation_id
  WHERE r.batch_kind='index'
)
SELECT
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('news_projection_generations','news_projection_index','news_projection_details',
   'news_projection_batches','news_projection_receipts_v2','news_projection_state',
   'news_projection_counts')) AS projection_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='index' AND name IN
  ('news_projection_generations_state_idx','news_projection_index_ordinal_idx',
   'news_projection_index_page_idx','news_projection_index_category_idx',
   'news_projection_index_review_page_idx',
   'news_projection_index_review_category_page_idx')) AS projection_indexes,
 (SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN
   ('legacy_news_current_index_delete_fence','legacy_news_current_detail_delete_fence',
    'legacy_news_noncurrent_index_insert_fence','legacy_news_current_index_update_fence',
    'legacy_news_v4_current_index_delete_fence',
    'legacy_news_v4_current_detail_delete_fence'))
   AS projection_triggers,
 (SELECT count(*) FROM pragma_table_info('news_projection_counts') WHERE name IN
  ('generation_id','review_state','category','item_count','parsed_count','candidate_expiries')) AS projection_count_columns,
  (SELECT count(*) FROM pragma_table_info('news_projection_receipts_v2') WHERE name IN
   ('generation_id','batch_kind','batch_offset','item_count','payload_hash',
    'receipt_digest','identity_digest','identity_keys_json','items_json','updated_at')) AS projection_receipt_columns,
 (SELECT count(*) FROM pragma_table_info('operator_retry_sync_state') WHERE name IN
   ('id','payload_digest','item_count','synced_at')) AS retry_columns,
 (SELECT count(*) FROM sqlite_master WHERE type='table'
   AND name='news_evidence_cleanup_budget') AS evidence_cleanup_budget_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='table'
   AND name='learning_record_counts') AS learning_count_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='index'
   AND name='learning_records_resource_identity_time_idx') AS learning_identity_indexes,
 (SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN
   ('learning_record_count_insert','learning_record_count_delete',
    'learning_record_count_identity_update')) AS learning_count_triggers,
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('dashboard_snapshots','news_index','news_details','news_evidence_records')) AS legacy_tables,
  coalesce((SELECT CASE WHEN json_type(payload,'$.recent_decisions')='array'
    THEN json_array_length(payload,'$.recent_decisions') ELSE 0 END
    FROM dashboard_snapshots WHERE id=1 AND json_valid(payload)),0) AS legacy_decisions,
  (SELECT count(*) FROM current_projection pi
    WHERE EXISTS(SELECT 1 FROM news_index li WHERE li.detail_key=pi.detail_key))
    AS legacy_current_index_count,
  (SELECT count(*) FROM current_projection pi
    WHERE EXISTS(SELECT 1 FROM news_details ld WHERE ld.detail_key=pi.detail_key))
    AS legacy_current_detail_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND NOT EXISTS(SELECT 1 FROM news_details ld WHERE ld.detail_key=li.detail_key))
    AS legacy_missing_detail_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND NOT (
        (json_extract(li.payload,'$.annotation_status')='NOT_REQUIRED'
          AND json_extract(li.payload,'$.model_visibility')='MODEL_INELIGIBLE'
          AND json_extract(li.payload,'$.parsed_at') IS NULL)
        OR (json_extract(li.payload,'$.annotation_status')='QUEUED'
          AND json_extract(li.payload,'$.model_visibility')='NOT_YET_PARSED'
          AND json_extract(li.payload,'$.parsed_at') IS NULL)
        OR (json_extract(li.payload,'$.annotation_status')='READY'
          AND json_extract(li.payload,'$.model_visibility')<>'NOT_YET_PARSED'
          AND json_extract(li.payload,'$.parsed_at') IS NOT NULL)
        OR (json_extract(li.payload,'$.annotation_status') IN
          ('REPAIRING_DISPLAY','BACKING_OFF','DEAD_LETTER','WAITING_CONTENT','CONTENT_UNAVAILABLE')
          AND json_extract(li.payload,'$.model_visibility')=
              json_extract(li.payload,'$.annotation_status')
          AND json_extract(li.payload,'$.parsed_at') IS NULL)))
    AS legacy_review_violation_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND li.parsed <> CASE
        WHEN json_extract(li.payload,'$.parsed_at') IS NOT NULL THEN 1 ELSE 0 END)
    AS legacy_parsed_flag_mismatch_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND li.model_candidate <> CASE
        WHEN json_extract(li.payload,'$.model_visibility')='MODEL_VISIBLE' THEN 1 ELSE 0 END)
    AS legacy_candidate_flag_mismatch_count,
  (SELECT count(*) FROM (
    SELECT cluster_id FROM news_index li
     WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
     GROUP BY cluster_id HAVING count(*) > 1))
    AS legacy_duplicate_cluster_count,
  (SELECT count(*) FROM news_index li
    WHERE li.detail_key IN (
      SELECT detail_key FROM news_index
       WHERE COALESCE(json_extract(payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      EXCEPT SELECT detail_key FROM current_projection))
    AS legacy_extra_current_index_count,
  (SELECT count(*) FROM (
    SELECT detail_key,category,cluster_id,published_time,collector_first_seen_time,
           parsed,model_candidate,impact_expires_at,mirror_contract,payload
      FROM news_index
     WHERE COALESCE(json_extract(payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
    EXCEPT
    SELECT detail_key,category,cluster_id,published_time,collector_first_seen_time,
           parsed,model_candidate,impact_expires_at,mirror_contract,payload
      FROM current_projection))
    AS legacy_current_row_mismatch_count,
  coalesce((SELECT item_count FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_all_count,
  coalesce((SELECT sum(item_count) FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state<>'ALL' AND c.category=''),-1) AS summary_review_count,
  coalesce((SELECT sum(item_count) FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id AND c.category<>''),-1) AS summary_category_count,
  coalesce((SELECT parsed_count FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_parsed_count,
  coalesce((SELECT CASE WHEN candidate_expiries='' THEN 0 ELSE
      1 + length(candidate_expiries) - length(replace(candidate_expiries,char(10),'')) END
    FROM news_projection_counts c WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_candidate_count,
  (SELECT coalesce(sum(parsed),0) FROM current_projection) AS current_parsed_count,
  (SELECT count(*) FROM current_projection i
    WHERE i.model_candidate=1) AS current_candidate_count,
  (SELECT count(*) FROM current_projection i
    WHERE i.model_candidate=1
      AND (i.impact_expires_at IS NULL OR length(i.impact_expires_at)<>32
        OR substr(i.impact_expires_at,27)<>'+00:00')) AS invalid_candidate_expiry_count,
  s.projection_state,s.active_generation_id,s.snapshot_id,s.source_digest,s.receipt_digest,
 s.index_count,s.detail_count,s.missing_detail_count,s.invariant_violation_count,
 g.state AS generation_state,g.contract_version AS generation_contract_version,
 g.watermark AS generation_watermark,g.activated_at AS generation_activated_at,
 g.expected_receipt_digest,g.staged_index_count,g.staged_detail_count
FROM news_projection_state s JOIN news_projection_generations g
 ON g.generation_id=s.active_generation_id WHERE s.id=1
