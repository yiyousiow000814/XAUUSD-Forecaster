# Single Gemma display review

User-authorized replacement: Gemini semantics -> one Gemma display review/edit ->
accepted annotation. No language ratio, Latin grounding, numeric-spelling repair,
model fallback or validator-driven display retry. Gemma returns the existing
four display fields, unchanged when satisfactory, translated when needed.
Semantic fields, raw source, record identity and historical evidence are unchanged.

The annotation request pool owns this request through the existing accounted
model gateway. Existing queued display checkpoints reuse their semantic result;
read-only checkpoint recovery remains until existing work drains. Old failure
rows are audit only. No new queues, states, fallback or recovery platform.
Network/JSON failures retain existing transport handling, not a new policy.

Impact: request pool -> annotation writer -> semantic validator -> dashboard.
Both producer and writer lose style gates so accepted Gemma text is not vetoed
later. Title-only translation also uses its selected model once without fallback.
Review cannot mutate semantic identities; only display keys are applied.

Tests: actual request pool with fake provider envelopes checks exactly one Gemma
review, accepted proper names, edited text, unchanged semantics, checkpoint reuse,
and title single-route behavior. Remove tests for retired style heuristics, retain
schema, causality, source evidence and historical immutability coverage. Run the
related annotation/semantic families, architecture generation, required CI and one
real queued-item recovery after normal main publication. No manual database edits.

## Retired test responsibilities

The following tests enforced the superseded deterministic display policy. Their
language rejection, exact spelling repair, validator retry and fallback requirements
are retired; single Gemma acceptance and immutable semantics replace them.

- test_chinese_display_accepts_bounded_grounded_identity_variants
- test_chinese_display_accepts_source_grounded_actor_and_character_names
- test_chinese_display_fields_share_declared_identity_context
- test_chinese_repair_policy_preserves_natural_english_identifiers
- test_display_checkpoint_accepts_declared_latin_company_names_without_model_call
- test_display_checkpoint_accepts_grounded_names_without_model_call
- test_display_checkpoint_accepts_source_grounded_episode_titles_without_model_call
- test_display_checkpoint_recomputes_stale_invalid_field_list
- test_display_checkpoint_revalidates_before_spending_another_model_call
- test_display_checkpoint_translates_ungrounded_etf_without_model_call
- test_display_failure_withholds_semantics_until_readable_output_exists
- test_display_number_recovery_does_not_merge_date_comma_with_year
- test_display_number_validation_accepts_natural_chinese_currency_order
- test_display_number_validation_rejects_unit_or_currency_conversion
- test_display_recovery_preserves_source_grounded_etf
- test_display_repair_preserves_request_failure_classification
- test_display_schema_bounds_are_repaired_before_model_admission
- test_failed_display_repair_withholds_annotation_and_records_failure_fields
- test_gemini_accepts_chinese_primary_prose_with_natural_english_names
- test_gemini_accepts_source_numbers_with_aggregator_spacing
- test_gemini_indonesian_named_month_does_not_become_unresolved_number
- test_gemini_locally_recovers_unverifiable_display_numbers
- test_gemini_named_month_translation_accepts_space_before_month
- test_gemini_named_month_translation_does_not_invent_numeric_month
- test_gemini_rejects_english_or_latin_prose_dominating_chinese
- test_gemini_rejects_non_chinese_non_latin_scripts
- test_gemini_rejects_non_chinese_translation_fields
- test_gemini_repairs_mixed_language_summary_with_counted_request
- test_gemini_repairs_mixed_script_story_identity_with_counted_request
- test_gemini_restores_source_number_lexemes_and_rejects_invention
- test_gemini_validates_semantic_reason_as_chinese_primary_display
- test_invalid_display_fields_include_numeric_siblings
- test_source_grounded_latin_span_family_accepts_references
- test_source_words_cannot_bypass_chinese_primary_validation
- test_story_title_matches_declared_identities_across_safe_punctuation_and_case
- test_title_translation_rejects_missing_or_unresolved_numbers
- test_title_translation_requires_every_source_number
- test_v16_distinguishes_translated_prose_from_english_identifiers
- test_v16_source_grounded_latin_span_family_rejects_prose_and_spoofs
- test_v17_accepts_exact_source_grounded_latin_without_semantic_classification
- test_v17_allows_ordinary_layout_whitespace
- test_v17_controlled_xauusd_exemption_is_closed
- test_v17_derives_multiple_disjoint_visible_latin_runs
- test_v17_does_not_semantically_reject_grounded_lowercase_latin
- test_v17_latin_identifier_list_can_still_be_english_dominant
- test_v17_mixed_identifiers_count_only_latin_letters
- test_v17_newline_terminates_independent_latin_runs
- test_v17_number_dense_chinese_has_zero_digit_language_weight
- test_v17_pure_digits_contribute_zero_english_language_weight
- test_v17_rejects_bracketed_source_grounded_english_dominant_field
- test_v17_rejects_invisible_or_bidi_control_inside_latin_run
- test_v17_rejects_ungrounded_or_partial_token_latin
- test_v17_repeated_source_occurrences_use_first_exact_coordinates
- test_v17_source_grounding_does_not_bypass_field_language_balance
- test_v17_story_title_rejects_ungrounded_latin
- test_v17_unicode_lookalike_fails_even_when_present_in_source

The perpetual retry exceptions are retired; the existing general provider and
model-output failure contract still applies. Its existing family tests remain.

- test_checkpointed_display_failure_remains_repairable
- test_checkpointed_display_provider_outage_never_becomes_terminal

## Boundary and execution review

| Boundary | Behavior and evidence |
| --- | --- |
| Python scheduler -> task registry -> annotation pool | Existing production entrypoint and accounted gateway; title route contains Gemma only. Scheduler tests exercise output failure without account fallback and quota deferral. |
| Gemini -> Gemma -> writer | Four display fields are editable; semantic fields and raw source identity are unchanged. Shared current schema no longer imposes display length or language heuristics. Output has the existing 2600-token response budget. |
| Old saved state -> new runtime | Read the exact raw-hash checkpoint and review its saved draft once. Do not reapply old rejection evidence or write new repair checkpoints. |
| New state -> readers/restart | No schema migration or new state. Accepted annotations remain append-only. Existing scheduler owns restart, leases and ordinary provider failures. |
| Failure before annotation commit | No success receipt or fabricated annotation. Existing transport/JSON handling applies; no special infinite display retry. |
| External dependency | Gemma output is advisory translation, not guaranteed perfect prose. Prompt guidance replaces local linguistic thresholds. Availability remains external; no alternative model is introduced. |

The selected production checkpoint was exercised with the actual configured
Gemma route and durable quota accounting. Two attempts returned Google HTTP 500
`INTERNAL`; the second response body confirmed `Internal error encountered.`
No annotation was written. The local row-read setup error and both provider
failures are retained outside the repository in the connected-recovery evidence.
This is not a successful live acceptance and does not establish why Google failed.

Focused annotation/semantic tests passed (165 before the final length cases);
scheduler/gateway/critical-state tests passed (239). Full Python and exact-head
CI results will be recorded separately. No production activation is claimed.

Full Python execution: 2088 passed, 7 skipped, one stale prompt-text assertion
for the explicitly retired Latin gate. Removed those two obsolete assertions
while retaining the source-evidence and semantic contract assertions. The final
focused run covers this correction and the newly added production-caller
checkpoint-to-writer test. This is not a claim that an earlier full run passed.

Residual checkpoint tables/writer schema and historical error-label decoding
remain solely to preserve/read audit data and construct historical fixtures.
They are not invoked to create new display-repair work. The active checkpoint
consumer reads saved semantics directly; it no longer queries or reapplies
historical rejection messages. Independent external review and live acceptance
are not replaced by this implementation review.
