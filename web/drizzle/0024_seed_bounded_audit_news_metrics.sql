-- Seed the bounded audit summary during the single-owner release handover.
-- Stable continues to own the legacy audit snapshot until Promote; Candidate
-- must not read that growing document on every public status request.
INSERT INTO `dashboard_snapshots` (`id`, `payload`, `received_at`)
SELECT 9,
       json_object(
         'generated_at', json_extract(`payload`, '$.generated_at'),
         'news_metrics', json(json_extract(`payload`, '$.news_metrics')),
         'daily_news_brief_summary', json(json_extract(`payload`, '$.daily_news_brief_summary')),
         'storyline_summary', json(json_extract(`payload`, '$.storyline_summary')),
         'news_evidence_summary', json(json_extract(`payload`, '$.news_evidence_summary')),
         'news_feature_policy', json(json_extract(`payload`, '$.news_feature_policy')),
         'news_evidence_resource', '/api/news-evidence',
         'audit_briefs_resource', '/api/audit-briefs',
         'audit_stories_resource', '/api/audit-stories',
         'audit_decisions_resource', '/api/audit-decisions'
       ),
       `received_at`
  FROM `dashboard_snapshots`
 WHERE `id` = 4
   AND json_valid(`payload`)
   AND json_type(`payload`, '$.news_metrics') = 'object'
ON CONFLICT(`id`) DO UPDATE SET
  `payload` = json_set(
    `dashboard_snapshots`.`payload`,
    '$.news_metrics',
    json(json_extract(`excluded`.`payload`, '$.news_metrics'))
  )
WHERE json_valid(`dashboard_snapshots`.`payload`)
  AND json_type(`dashboard_snapshots`.`payload`, '$.news_metrics') IS NULL;
