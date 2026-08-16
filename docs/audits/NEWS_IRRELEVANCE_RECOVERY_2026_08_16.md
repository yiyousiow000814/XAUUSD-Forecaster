# Legacy News Irrelevance Recovery Audit

## Scope

This point-in-time audit covers 438 current-prompt annotations persisted from
2026-08-12 through 2026-08-15 with the legacy invalid marker
`语言或结构一致性检查未通过`. Their immutable identity cohort has SHA-256
`d1448e54d3c4c9d00cc83837b0ad953f9a6b6f59ddb10fd9eab84d8d3a029683`.

The cohort contains 314 GDELT discoveries, 59 Google News Fed/rates items,
34 Google News gold-context items, 29 Google News US-employment items, one
Federal Reserve press item, and one US Treasury press item.

## Finding

These 438 `IRRELEVANT` values were not trustworthy semantic decisions. An old
display-language fallback manufactured a neutral irrelevant annotation after
validation failed. The later recovery path correctly excluded those rows from
model permission, but the scheduler still treated the mere presence of an
annotation row as completed work. The public mirror consequently retained many
of them as permanently processing.

The audit used two blind structured reviews over the stored source text. The
second review used the fallback model and a deliberately skeptical relevance
boundary. Every non-confirmed result was then inspected against the source and
the News Evidence Contract. Full source bodies and rejected model output remain
in the private evidence ledger; they are not copied into this public report.

Final adjudication:

| Legacy `IRRELEVANT` decision | Articles | Share |
| --- | ---: | ---: |
| Defensible | 371 | 84.7% |
| Incorrect; requires semantic recovery | 63 | 14.4% |
| Boundary remains genuinely ambiguous | 4 | 0.9% |

The incorrect group includes current bullion-price reports, central-bank gold
actions, US CPI and employment evidence, material Federal Reserve policy news,
and reports that explicitly connect USD, yields, or a major geopolitical shock
to gold. Defensible exclusions are dominated by company-level mining and
jewellery stories, gold loans, products or brands named Gold, sports medals,
local lifestyle news, and articles where macro language is incidental.

The four ambiguous records concern household-credit context, a large private
gold holder, private physical-gold accumulation, and regional gold-market
infrastructure. They are safe to re-run rather than grant model permission from
this audit alone.

## Prompt assessment

The incident itself was caused by persistence and scheduling code, not by a
normal model relevance answer. The current v15 prompt nevertheless leaves three
boundaries too implicit:

1. A company mining, lending against, selling, or mentioning gold is not a
   direct bullion event without market-wide supply, demand, reserve, flow, or
   price transmission.
2. Incidental references to rates, jobs, inflation, war, or the Fed do not make
   a company, consumer, or investment-list article a macro driver.
3. `DIRECT` or `MACRO_DRIVER` needs exact source evidence for both the current
   event and its credible XAUUSD transmission; topic proximity is insufficient.

A v16 prompt should state those negative boundaries and add matched positive
and negative examples. It must be introduced as a versioned generation
handover, not silently substituted under the v15 identifier. That handover
should follow recovery of this cohort so its quota cost and classification
delta can be measured separately.

## Recovery acceptance

- Invalid legacy annotations do not satisfy an active annotation job.
- A completed job is reopened only when the canonical pending-query contract
  still selects its immutable revision.
- Recovery preserves prior operational attempt receipts and starts a fresh
  semantic failure budget in the immutable failure ledger.
- Any annotation update advances the news mirror cursor, including an invalid
  annotation that does not have model permission.
- A mirror-contract revision replays the bounded 60-day archive and removes
  stale processing rows.
- Terminal model failures expose a bounded reason code and a safe Chinese
  explanation without publishing rejected output.
