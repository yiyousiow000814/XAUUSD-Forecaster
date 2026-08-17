# News Candidate Retrieval Evaluation Protocol

## Purpose

This protocol evaluates the point-in-time candidate layer before Gemma makes an
event-identity decision. It separates retrieval failures from final-model
classification failures and prevents successful historical resolutions from
being mistaken for evidence of complete recall.

## Frozen population

Every benchmark revision contains exactly 100 positive pairs and 100 hard
negative pairs. Each pair names immutable annotation IDs and verifies both
source-content hashes before scoring.

Positive labels are independently reviewable:

- collector syndication clusters may establish the same verifiable report
  without using Gemma identity output; and
- cross-cluster pairs require a recorded evidence review of the stable
  occurrence, reference period, and material change.

Hard negatives must preserve meaningful overlap while naming a different
occurrence or release family. Easy pairs from unrelated topics do not qualify.
The manifest records the label basis and an audit note for every pair.

## Replay boundary

The evaluator MUST:

1. open the evidence database read-only;
2. use the production candidate-universe loader, index, admission, and ranking
   functions rather than a benchmark-only approximation;
3. exclude records received after the current item;
4. verify the frozen content hashes;
5. make no provider or final-model request; and
6. emit a digest of the exact point-in-time candidate universe.

Semantic candidates MUST be scored from a complete stored vector namespace
bound to the declared embedding-text version and exact model digest. Benchmark
execution must not create missing embeddings or contact the embedding service.

The benchmark reports Recall@1, Recall@5, MRR@5, positive empty-candidate rate,
relation slices, and hard-negative exposure at five. Hard-negative exposure is
candidate noise, not an end-to-end false-positive rate; the final identity
model is intentionally outside this protocol.

## Change gate

A retrieval change is eligible for production verification only when it:

- does not reduce overall or per-relation Recall@5;
- does not increase the positive empty-candidate rate;
- does not increase hard-negative exposure at five; and
- records route-level ablations so a gain cannot be attributed to an
  unmeasured implementation change.

Recall gains MUST be reported independently from final Gemma decisions. A
prompt improvement cannot satisfy a candidate-retrieval gate.

## Command

```powershell
python scripts/audit_news_candidate_retrieval.py `
  --database C:\path\to\forward-evidence.sqlite3 `
  --manifest tests/fixtures/news_candidate_retrieval_benchmark.json `
  --mode hybrid `
  --output .local/news-candidate-retrieval-result.json
```
