# News Identity Retrieval Design

## Objective

The identity model must receive the strongest point-in-time prior evidence even
when extraction aliases or wording differ. Retrieval therefore combines
independent routes instead of treating one Gemini-generated field as a hard
gate.

## Routes

The active `news-hybrid-retrieval-v2` generation uses:

1. deterministic recall from collector clusters, material-event keys,
   episode keys, normalized token signatures, and canonical actors;
2. multilingual lexical recall over the headline, bounded summary, structured
   identity fields, and cited evidence excerpts;
3. asymmetric semantic recall from `gemini-embedding-2`; and
4. deterministic reciprocal-rank fusion that preserves exact identity anchors
   while allowing lexical and semantic candidates into the bounded context.

The first three routes produce a union. They are not vetoes. The final identity
model still decides `SAME_EVENT`, `SAME_EPISODE`, `NEW_EPISODE`, or
`UNRESOLVED` from the supplied evidence.

## Point-in-time and persistence boundaries

- A candidate received after the current annotation is never eligible.
- The current source revision is never its own candidate.
- Core facts can only use action-bearing core facts or evidence documents as
  same-event anchors.
- Embedding text is versioned and bound to a stable provider/model/task profile.
- Vectors and retrieval receipts are append-only. A receipt records every route
  ranking, the final selected IDs, and a digest of the candidate universe.
- Model-tag or text-contract changes create a new vector namespace; they do not
  reinterpret old vectors.

## Operational behavior

The initial historical universe must be backfilled before hybrid impact work is
enabled:

```powershell
python scripts/backfill_news_identity_embeddings.py `
  --database C:\path\to\forward-evidence.sqlite3
```

Before retrieval, the runtime appends a bounded catch-up batch across the whole
point-in-time candidate universe, including annotations that became eligible
between deployment backfill and the next impact cycle. If a larger migration is
still incomplete, impact work is deferred with
`NEWS_EMBEDDING_BACKFILL_PENDING`; this is maintenance progress rather than an
identity decision. Provider batches are admitted against each independent
account's 100 RPM, 30K TPM, and 1K RPD limits. Each embedded content item counts
as a request even when several items share one HTTP envelope. If capacity is
temporarily unavailable, work is deferred instead of bypassing the scheduler.

The dedicated embedding model is retrieval infrastructure, not an Assistant
chat model. Historical Qwen vectors and receipts remain immutable audit
evidence but are not active runtime inputs.

## Promotion evidence

Every retrieval generation must pass
[the candidate retrieval evaluation protocol](../protocols/NEWS_CANDIDATE_RETRIEVAL_EVALUATION.md).
Route-level ablations must be retained so a combined gain cannot hide a weak or
unavailable route.
