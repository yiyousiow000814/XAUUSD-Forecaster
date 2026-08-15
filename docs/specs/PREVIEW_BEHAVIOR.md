# Preview Behavior Specification

This is the main entry point for understanding branch Preview behavior. Hard
safety and correctness guarantees are defined in
[`PREVIEW_ISOLATION.md`](../contracts/PREVIEW_ISOLATION.md).

## What is fixed and what may change

The branch and commit code artifact is fixed for a deployment. Its immutable
build snapshot is also fixed. Bounded, explicitly read-only production data may
change after deployment when freshness is part of the feature being reviewed.
Neither data mode grants runtime, write, model-promotion, or trading authority.
Neither mode grants permission to consume production model capacity or create
production Assistant state.

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
Factor coverage is currently recomputed by the branch from its bounded captured
inputs. A grouped storyline output is not complete replay input: a storyline-
logic change must capture the complete bounded or paged independent-event input
required by that policy before the Preview can claim to verify the change.

### Snapshot fallback

When an allowed current read fails, the interface may retain or return the
immutable build snapshot where that snapshot is valid for the feature. The
fallback must be identified as a snapshot and must not be presented as current.
Unknown values remain unknown; they are not converted to zero.

Some features require an authoritative complete current view. For example, a
failed current archive read must not be replaced by a smaller recent window that
looks complete. Such a feature returns an unavailable state instead.

### Assistant presentation fixtures

When an Assistant surface exists, a branch Preview may use explicitly labeled
synthetic fixtures or an immutable build snapshot to review layout, streaming
presentation, and content blocks. It cannot submit model-consuming work, create
production conversations, or present fixture output as a live grounded answer.

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
  to their immutable build snapshots. Machine-readable field provenance marks
  the status keys that remain branch build snapshots; factor coverage is the
  current branch-recomputed example.
- The news index reads the bounded current D1 archive. If that complete archive
  is unavailable, Preview reports it unavailable rather than substituting a
  partial relay window.
- A news detail embedded in the build snapshot is used first; other requested
  details may be read from D1.
- Market chart and paged market or learning history currently use bounded data
  frozen into the build artifact.
- Production-precomputed storylines may support presentation review, but they do
  not prove a changed branch grouping policy. Such a change requires complete
  independent-event replay input rather than reverse-engineering grouped output.
- Assistant presentation may use labeled synthetic or frozen fixtures; its
  model-consuming and conversation-mutating routes remain unavailable.
- `/api/assistant-chat` returns a labeled synthetic empty turn or finite empty
  `assistant.event.v1` stream for reads. POST and machine-claim modes reject
  before identity, body, D1, queue, or model work.

## Machine-readable manifest

`web/preview-manifest.json` contains build configuration: resource paths, the
bounded initial news page size, status keys retained for first paint, and status
keys whose values come from the branch build snapshot. It is a manifest, not
the source of safety guarantees. Compatibility-sensitive changes to its fields
must remain atomic across the builder and web consumers.
