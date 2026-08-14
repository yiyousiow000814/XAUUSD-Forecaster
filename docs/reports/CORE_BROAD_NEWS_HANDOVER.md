# Core and Broad News Handover Report

## Problem

The narrow news lane was defined by a fixed official-source subset. This made
an otherwise complete first-party release or independently corroborated event
ineligible solely because its collector was absent from that subset. It also
left the narrow model in a zero-effect cold start while the Broad model could
see the same qualified evidence.

## Contract

- Core accepts complete first-party evidence or one event confirmed by at
  least two independent reliable publishers.
- Broad is a strict superset of Core and may additionally admit a single
  identified publisher at its bounded evidence weight.
- Both lanes require the same point-in-time clock, complete body, canonical
  event identity, material update, semantic relevance, and lifetime checks.
- Gemini and Gemma describe event semantics. Deterministic evidence attributes
  enforce time, identity, deduplication, and lane membership.
- There is no separate Core source allowlist. A registered first-party
  identity is one evidence attribute; independent corroboration provides the
  second path into Core.

## Handover

The new contract has a distinct feature, eligibility, and policy version.
Existing predictions and evidence remain immutable. Point-in-time snapshots
may be appended for mature historical decisions, but predictions are never
backfilled. Training builds one complete Core/Broad generation and activation
switches all members atomically after artifact validation.

The stable database identities `NEWS_RESIDUAL` and `FULL` continue to identify
the narrow lane across historical versions. Under the new contract their model
versions, manifests, UI labels, and audit documentation identify that lane as
Core. The V2 storage tokens `OFFICIAL_MODEL` and `OFFICIAL` remain only because
they are immutable schema values already present in historical evidence.

## Acceptance

- Every Core event is also Broad.
- First-party evidence is not tied to the former official-source subset.
- Two independent reliable publishers can qualify the same event for Core.
- One reliable publisher cannot qualify Core by itself.
- The active generation matches the exact Core/Broad contract triple and
  publishes the complete model set.
