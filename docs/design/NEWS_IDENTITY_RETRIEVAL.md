# News Identity Retrieval Design

## Objective

The identity model must receive the strongest point-in-time prior evidence even
when extraction aliases or wording differ. Retrieval therefore combines
independent routes instead of treating one Gemini-generated field as a hard
gate.

## Routes

The active `news-hybrid-retrieval-v1` generation uses:

1. deterministic recall from collector clusters, material-event keys,
   episode keys, normalized token signatures, and canonical actors;
2. multilingual lexical recall over the headline, bounded summary, structured
   identity fields, and cited evidence excerpts;
3. local semantic recall from `qwen3-embedding:0.6b`; and
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
- Embedding text is versioned and bound to the exact local Ollama model digest.
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

The runtime embeds each newly annotated current record before retrieval. If the
local model is absent, its digest changes without a backfill, or any historical
vector is missing, identity assessment fails closed and returns to scheduler
retry instead of silently claiming complete hybrid context.

The dedicated embedding model is retrieval infrastructure. Qwen and Ministral
chat profiles do not generate these vectors, and no Google quota is consumed.

## Promotion evidence

Every retrieval generation must pass
[the candidate retrieval evaluation protocol](../protocols/NEWS_CANDIDATE_RETRIEVAL_EVALUATION.md).
Route-level ablations must be retained so a combined gain cannot hide a weak or
unavailable route.
