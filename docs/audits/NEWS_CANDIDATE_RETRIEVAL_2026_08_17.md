# News Candidate Retrieval Baseline — 2026-08-17

## Scope

This audit replayed the deterministic candidate layer over immutable historical
news without calling Gemma. The frozen manifest contains 100 positive pairs and
100 hard negatives. Sixty-eight positives use collector syndication clusters,
which are independent of Gemma identity output. Thirty-two cross-cluster pairs
were reviewed from the stored headlines and bounded claim evidence. The hard
negatives pair distinct U.S. macro or Federal Reserve release families.

The candidate universe contained 2,556 current-contract annotations. The run
verified every current and prior content hash and preserved each pair's receipt
ordering.

## Baseline

| Metric | Result |
| --- | ---: |
| Recall@1 | 77.0% |
| Recall@5 | 93.0% |
| MRR@5 | 0.8445 |
| Positive empty-candidate rate | 3.0% |
| SAME_EVENT Recall@5 | 92.31% |
| SAME_EPISODE Recall@5 | 100.0% |
| Hard-negative exposure@5 | 27.0% |

Seven known positive pairs were outside the production top five. Four were not
recalled at any rank within the 20-item audit window, including three empty
candidate results. This confirms that final Gemma reasoning is not the current
quality ceiling: the correct prior evidence can be absent before Gemma runs.

Hard-negative exposure is not the final false-positive rate. It measures how
often a known different release family consumes the bounded candidate context;
Gemma may still reject that candidate correctly.

## Provenance

- Manifest schema: `news-candidate-retrieval-benchmark.v1`
- Manifest digest: `39c391f86c7e75a84238eeb31632b003a3179018b38ea0cab38c508c635225fd`
- Candidate-universe digest: `13505d696fe947b8640d9f32ac328a54b5800b1d50356e59f8dca2b84d7bf901`
- Candidate limit: 10,000
- Production context limit: 5 candidates

The result is a point-in-time baseline, not a production promotion. The next
change must compare deterministic-only, lexical, semantic, and combined routes
against this same manifest.

## Hybrid candidate

The `news-hybrid-retrieval-v1` candidate was replayed over the same manifest
after a complete append-only backfill with local
`qwen3-embedding:0.6b` digest
`ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`.
The benchmark itself read stored vectors and made no provider request.

| Route | Recall@1 | Recall@5 | MRR@5 | Empty | Hard-negative exposure@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Deterministic | 77% | 93% | 0.8445 | 3% | 27% |
| Lexical | 54% | 74% | 0.6200 | 0% | 2% |
| Semantic | 60% | 85% | 0.7100 | 0% | 3% |
| Combined | 83% | 96% | 0.8892 | 0% | 14% |

The combined route passed every promotion gate: it improved Recall@1,
Recall@5, and MRR@5, removed empty positive results, and reduced hard-negative
exposure. Neither lexical nor semantic retrieval is strong enough to replace
the deterministic route independently.
