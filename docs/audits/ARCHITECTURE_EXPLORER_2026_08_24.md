# Private Architecture Explorer Audit — 2026-08-24

## Boundary

The Explorer is a private, static Admin presentation. Its only architecture
source is `architecture/manifest.json`.

```text
architecture/manifest.json
  -> bounded build-time loader
  -> Vite replacement used only by the lazy view
  -> /admin/architecture prerender plus ArchitectureExplorerView chunk
```

There is no Architecture API, D1 table, Worker route, GitHub runtime request,
Markdown parser, Windows process, background thread, or production mutation.

## Manifest inventory

- Schema: `architecture-explorer-v1`
- Views: 11
- Nodes: 24
- Edges: 28
- Serialized bytes: 28,446 of the fixed 65,536-byte limit
- Runtime and implementation states are separate fields.
- Detailed Markdown contracts remain authoritative.

## Bundle evidence

| Artifact | Repaired #301 parent | Explorer working tree | Delta |
|---|---:|---:|---:|
| `DashboardApp` chunk | 28,696 bytes / 10,157 gzip | 29,117 bytes / 10,250 gzip | +421 / +93 gzip |
| shared `index` chunk | 217,233 bytes / 58,792 gzip | 217,752 bytes / 58,836 gzip | +519 / +44 gzip |
| lazy Explorer chunk | absent | 34,827 bytes / 9,911 gzip | lazy only |

The public initial shared chunks increase by 940 raw bytes and 137 gzip bytes
for route/navigation wiring. The 28,446-byte manifest exists only in the lazy
Explorer chunk and does not inflate the public Live initial payload.

## Local responsive QA

| Viewport | Horizontal overflow | Visible targets under 44px | Layout |
|---|---|---|---|
| 1440x900 | none | none | Three-pane rail, architecture lane, and detail inspector |
| 390x844 | none | none | View selector, one-column cards, textual dependencies, details below |
| 360x800 | none | none | View selector, one-column cards, textual dependencies, details below |

Verified first Overview, view drill-down, keyboard node selection, owner/path/
test/tag search, runtime-state filtering, breadcrumb updates, upstream,
downstream, unaffected-components text, and exact-SHA GitHub links. The mobile
flow selected the Campaign view, filtered `PENDING`, selected the Explorer,
scrolled to details, and retained a readable non-miniaturized layout.

The Explorer made no Architecture API request and no third-party request.
Local status/session probes cannot succeed without Cloudflare bindings; deployed
Preview request verification remains a separate exact-head gate. Browser console
errors/warnings were empty, the temporary viewport override was reset, and the
final task-created browser session count was zero.
