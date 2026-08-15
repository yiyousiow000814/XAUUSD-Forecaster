# Preview Isolation Contract

This document defines the non-negotiable safety and correctness boundaries for
branch Previews. For observable behavior and data modes, see
[`PREVIEW_BEHAVIOR.md`](../specs/PREVIEW_BEHAVIOR.md).

## Branch artifact identity

- A branch Preview represents one branch and commit artifact.
- The running code artifact and displayed commit identity must not silently
  drift apart.
- Preview identity must remain distinguishable from production in the user
  interface and machine-readable responses.

## No production mutation

A Preview must never mutate production-owned state, including:

- production D1;
- evidence ledgers;
- dashboard synchronization state;
- collector or runtime state;
- trading or order state;
- model activation or promotion state;
- scheduler state;
- Assistant conversations, messages, memory, or queue state;
- production configuration; or
- any other authoritative production-owned state.

All Preview access to production data must cross an explicitly read-only
boundary. Every Preview-exposed write route must reject the request before
authentication, parsing, storage access, or another side effect can occur.

## No runtime or trading authority

Reading current production data must never represent the Preview itself as:

- the online production runtime;
- an active collector;
- a broker-connected runtime;
- a trading-capable runtime; or
- the source of production decisions.

A Preview is always non-trading and non-authoritative. Production data shown by
a Preview remains production-derived evidence, not evidence of branch runtime
activity.

## Provenance honesty

A Preview must not present:

- an immutable build snapshot as current data;
- a partial window as a complete archive;
- production-precomputed output as proof that changed branch logic works;
- an unavailable or unknown value as zero; or
- a stale value as authoritative current data.

Fallback and partial data must retain machine-readable provenance and a visible
state where a viewer could otherwise mistake them for current authority.

## Branch implementation verification

When a pull request changes logic under review, Preview must exercise the branch
implementation. Suitable production-derived input may be read or captured, but
the changed transformation, grouping, interpretation, policy, or other
computation must run from the branch artifact before its output is used as
verification.

Production-precomputed output must not bypass the changed branch logic when that
would prevent the change from being reviewed.

Grouped, summarized, or otherwise lossy output is not complete replay input.
A Preview must not reconstruct missing independent events from that output and
then describe the result as verification of changed grouping logic. Logic-level
verification requires the complete bounded or paged source records governed by
the changed contract.

## No production model consumption

A branch Preview must not spend production model capacity, use production model
credentials, or create production Assistant work. Assistant write and
model-consuming routes reject before authentication, parsing, storage access,
queue admission, or provider transport.

A Preview may render explicitly labeled synthetic Assistant fixtures or an
immutable build snapshot for presentation review. Such output is not a live
conversation and does not verify the model, retrieval, or persistence path.

The responsive Assistant workbench may install its branch-owned fixture only
after the Preview API returns the labeled synthetic-empty Assistant state. The
fixture remains visibly labeled, never enters an API request, and may be used
only for local selection, paging, progress presentation, and responsive
rendering of the five validated v1 content-block types. Synthetic content and
links remain explicitly non-authoritative. Composer, rename, title regeneration,
archive, cancellation, and every other mutation remain disabled.

For `/api/assistant-chat`, every POST and machine claim returns the standard
write rejection before authentication, body parsing, or D1 access. Human turn
reads return a labeled synthetic empty object, and event reads return a finite
empty `assistant.event.v1` SSE response. Neither response probes production
Assistant ownership or state.
