# Cloudflare resource contract

The local append-only SQLite ledger remains the evidence source of truth. The
Cloudflare layer is a public, read-only materialized view with explicit storage
roles:

| Resource | Owns | Must not own |
| --- | --- | --- |
| Worker bundle | UI code, labels, a compact branch status snapshot | learning history, market history, article bodies |
| D1 | bounded/queryable status, learning snapshots, candles, decisions, news index and detail records | unbounded exports or raw archives |
| R2 | immutable large exports, compressed raw evidence archives, rebuild artifacts over 1 MiB | counters, filters, mutable current state |
| KV | small derived manifests, generation pointers and ETags (target <= 32 KiB) | evidence records, ordered history, high-write counters |

## Request memory rules

- Stream R2 object bodies directly to the response; do not call `arrayBuffer()`,
  `text()` or `json()` on an unbounded object in a Worker.
- Query D1 with prepared statements, explicit filters and limits. Historical
  charts page through normalized rows; they do not load the complete ledger.
- A Preview build may embed compact branch-specific status so code changes are
  visible, but its growing learning and market resources are read-only D1
  materializations. This prevents each isolate from parsing a multi-megabyte
  build constant.
- R2 and KV are optional tiers, not alternative sources of truth. A missing
  archive or cache entry must never change a model decision or evidence record.

## Current measured split (2026-08-11)

Before this contract, the Preview constant was about 1.88 MB: status 329 KB,
learning 739 KB, market chart 725 KB, news index 68 KB and 12 details 20 KB.
The complete learning and market-chart resources now stay in D1. Preview keeps
only a 209 KB first-paint learning summary, reducing the measured Worker
constant from about 1.88 MB to 626 KB (roughly 67%). New immutable payloads
above 1 MiB belong in R2 rather than being added back to the bundle or stored as
one growing D1 JSON value.
