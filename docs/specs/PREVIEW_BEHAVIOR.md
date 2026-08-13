# Preview Behavior Specification

This is the main entry point for understanding branch Preview behavior. Hard
safety and correctness guarantees are defined in
[`PREVIEW_ISOLATION.md`](../contracts/PREVIEW_ISOLATION.md).

## What is fixed and what may change

The branch and commit code artifact is fixed for a deployment. Its immutable
build snapshot is also fixed. Bounded, explicitly read-only production data may
change after deployment when freshness is part of the feature being reviewed.
Neither data mode grants runtime, write, model-promotion, or trading authority.

## Data modes

### Immutable build snapshot

The build process captures a bounded public production snapshot and freezes it
with the branch artifact. It supports:

- deterministic initial rendering;
- reproducible comparisons;
- fallback when current data is unavailable; and
- branch-side recomputation from captured input.

The snapshot itself never changes. This does not mean every datum displayed by
the Preview is frozen.

### Current read-only production data

A Preview may read bounded current public production data when realistic or
fresh verification requires it. Suitable examples include current status
metrics, bounded D1 archive totals, public learning summaries, bounded
histories, and news data.

These reads may refresh while visible when their feature requires freshness.
They must remain bounded, preserve Preview identity, and never make the Preview
look like the production runtime.

### Branch-side recomputation

When a pull request changes business, grouping, interpretation, transformation,
or policy logic, the preferred verification path is:

```text
production-derived input -> branch implementation -> Preview output
```

Production-precomputed output cannot substitute for the changed branch logic.
The storyline build replay is the current example: captured timeline input is
replayed through the branch's storyline policy before the Preview artifact is
built.

### Snapshot fallback

When an allowed current read fails, the interface may retain or return the
immutable build snapshot where that snapshot is valid for the feature. The
fallback must be identified as a snapshot and must not be presented as current.
Unknown values remain unknown; they are not converted to zero.

Some features require an authoritative complete current view. For example, a
failed current archive read must not be replaced by a smaller recent window that
looks complete. Such a feature returns an unavailable state instead.

## Semantic display states

- **Loading**: the current authority has not resolved yet.
- **Ready/current**: an authoritative current read succeeded.
- **Snapshot**: a valid immutable build-snapshot fallback is displayed.
- **Error/unavailable**: neither an authoritative current read nor a valid
  fallback can satisfy the requested value.

Animation and copy may change without changing these states.

## Refresh and polling

- Build-snapshot-only resources do not poll.
- Current read-only resources may refresh when their data contract requires
  freshness.
- Reads and responses remain bounded as stored history grows.
- Hidden or off-screen resources do not create unnecessary production load.
- Browser automation does not poll and must close its session after testing.

Refresh intervals are implementation settings unless promoted to an explicit
product requirement.

## Preferred mode by change type

| Change or condition | Preferred Preview mode |
| --- | --- |
| Presentation or UI-only change | Branch code with current bounded read-only production data |
| Business-logic change | Suitable production-derived input processed by the branch implementation |
| Current data unavailable | Immutable build snapshot with explicit snapshot labeling |
| Mutation or write path | Reject before any side effect |

## Current resource behavior

- Status and public learning summaries prefer current read-only D1 and fall back
  to their immutable build snapshots. Status overlays retain branch-recomputed
  storyline and factor-coverage output instead of replacing it with production-
  precomputed output.
- The news index reads the bounded current D1 archive. If that complete archive
  is unavailable, Preview reports it unavailable rather than substituting a
  partial relay window.
- A news detail embedded in the build snapshot is used first; other requested
  details may be read from D1.
- Market chart and paged market or learning history currently use bounded data
  frozen into the build artifact.
- The build snapshot replays captured storyline nodes through the branch
  storyline implementation so policy changes remain reviewable.

## Machine-readable manifest

`web/preview-manifest.json` contains build configuration: resource paths, the
bounded initial news page size, and status keys retained for first paint. It is
a manifest, not the source of safety guarantees. Compatibility-sensitive
changes to its fields must remain atomic across the builder and web consumers.
