# Current-source architecture

Run `python scripts/compile_architecture.py build`, then commit the generated
files. `python scripts/compile_architecture.py check` fails on drift without
rewriting anything. `python scripts/compile_architecture.py explain append_clock_event`
prints source call sites and side-effect syntax. PowerShell is required for the
real parser; inspected scripts are never dot-sourced or executed.
Node and the exact TypeScript package locked by `web/package-lock.json` are
required for selected TS/TSX sources. With no Web installation, run
`python scripts/architecture_typescript_tool.py` first. It installs only that
package with an npm-ci projection of the existing lock, SRI verification and
lifecycle scripts disabled; it does not install the Web application. A missing,
malformed or mismatched tool fails, never omits TypeScript coverage.
Obsolete generated files also fail check. Build preserves rather than silently
deleting them; review and remove the retired generated view when renaming it.

The first slice uses the AST extraction approach from PR #321, adapted to current
source rather than the obsolete classification branch. Its shared JSON and selected
Mermaid views are generated from the selection, source and tool bytes. Symbols
use path plus qualified name, independent of line movement; spans retain exact
locations. `syntactic_owner` names the source file, not process/data authority.

The optional `source_symbols` selection maps a selected file to exact parser
symbol IDs. The compiler still parses and hashes that entire file; it retains
the complete selected definitions, their lexical descendants and file import
syntax. Unlisted files keep their entire existing symbol inventory. Missing or
duplicate selectors, ambiguous parsed IDs and roots excluded by the scope fail
closed. This is explicit source selection, not inferred call closure: calls into
unindexed definitions remain UNKNOWN frontier edges without a candidate symbol.
No function body is located using regular expressions or line-range declarations.
Changing an unselected part still changes the whole-file input digest; parse
errors there cannot be hidden by the selection.

TS/TSX uses the pinned TypeScript compiler API (`createSourceFile` and AST
visitation), not regex call extraction. Functions, classes, methods, interfaces,
types, named object declarations and assigned callbacks retain qualified names
and source spans. Inline anonymous functions and object contexts use line/column-qualified labels;
these labels intentionally change when their source position changes. A callback
inside a variable's initializer call uses that variable as source context, not
proof that the wrapper returns or executes it. TypeScript columns are UTF-16
code-unit positions. Imports, JSX, object keys and literal/template call arguments
are syntax observations; the parser does not run application code, a bundler or
the TypeScript type checker. Dynamic imports, computed dispatch and runtime
configuration remain UNKNOWN. Import aliases are not guessed into resolved calls.

`allowed` contains view-selection declarations, not approved dependency edges.
`observed` contains source syntax only. Calls retain UNKNOWN runtime binding,
including same-file candidates. Literal SQL is retained without claiming query
plans, transitive writes, successful execution or atomicity. Python context
manager commits, native/PowerShell method invocation, decorators and dynamic
imports require additional analysis/runtime evidence. Absence of an extracted
edge does not prove absence of a side effect. `runtime` is explicitly UNKNOWN.

Input digests exclude outputs, commit SHA, machine paths and time. They normalize
UTF-8 BOM and CRLF/LF for Windows/Linux parity. The CI summary records source SHA
separately. Test sources are inputs but their presence is not a test PASS.
The parser identity includes TypeScript's exact version, resolved archive and
full SRI from the existing Web lock. Unrelated package-lock entries do not churn
that identity. Parser execution accepts only the executing tool checkout's fixed
Web installation or digest-keyed cache, including physical path containment
before reading package metadata. The retired `ARCHITECTURE_TYPESCRIPT_PACKAGE`
environment variable is ignored; it cannot select an installation root or provide
a missing-dependency fallback. The owning npm lock and installed version must also match
before parser execution. An explicit installer `--cache` is an acquisition output,
not authority for the parser to load that location. The isolated tool cache is keyed by the full
locked identity, not just the version. Acquisition errors are TOOL_UNAVAILABLE
or TOOL_INTEGRITY_FAILED; syntax failures remain ARCHITECTURE_PARSE_FAILED.
The npm lock is acquisition integrity, not a signed provenance claim for a
manually modified installation.

The shared JSON uses canonical compact UTF-8 serialization without deleting
fields. Both producer and browser reader enforce the unchanged 2MiB maximum.
Mermaid and `explain` retain readable projections; generated whitespace is not
source coverage.

This is DECLARED_CRITICAL_SLICES_ONLY, not the finished whole-system index or
transaction proof. The staged lifecycle remains the recovery evidence authority.
`Current source architecture` is now an enforced required main-branch check;
the coordinator verified the effective rule after merging #460. This source
increment does not change branch protection.
No old PR is superseded merely by this generated index.

## News / Worker / Audit selected view

The generated [News source view](generated/news-worker-audit.mmd) follows current
source, not the old classification branch. It selects three independent resource
families meeting at the Audit UI, **not** News CURRENT flowing into one Audit
snapshot:

| Resource | Local API / projection / Sync | Worker consumer | Audit UI consumer |
| --- | --- | --- | --- |
| News article generation | API `_build_news_projection_source`, frozen `/api/news-archive` manifest/batch handler; `news_projection.py::build_news_projection_generation`; `_sync_news` | News index/content route handlers and `news-projection-store.ts` | `AuditView.refreshNews`, content requests, `authoritativeNewsTotals` |
| Event / visibility evidence | API `_build_news_evidence_resource`, `_materialize_news_evidence_generation`, `/api/news-evidence` page handler; `_sync_news_evidence` | News evidence route and `news-evidence-store.ts` | `AuditView.refreshEvidence` |
| Audit summary / detail | API `main` registers `_optional_resource_payload` with `DashboardReadModelOwner`; GET reads the derived resource; `_sync_audit` | `api-router.ts` snapshot fast path | `AuditView.refreshAudit` / `refreshAuditDetail` |

News generation uses its own generation, snapshot and receipt identities;
evidence has a separate paged snapshot/cursor; Audit summary/details have separate
snapshot resources. Production Audit uses `SNAPSHOT_ROUTES`. News handlers use
the dynamic `import.meta.glob` dispatch, as does Preview routing; those runtime
bindings remain UNKNOWN. The current-source test checks their distinct declared
interfaces and call sites, not HTTP delivery, D1 state or a successful generation.

The local API scope retains twelve complete resource/launch definitions, not the
entire module. `Handler.do_GET` calls the frozen article source and batch helpers;
the older direct archive-page helper is not its production entry point. Article
generation uses `threading.Thread(target=_finish_news_projection_source_build)`;
the target is selected independently, not promoted into a direct-call edge.
Evidence uses a callback passed to `news_evidence_cache.get`, not the generic
Audit/Learning/Market read-model owner. Its freeze, publication and page helpers
remain separate from article CURRENT. The generic owner registration is visible
at API `main`; its internals and the broad `_dashboard_payload` builder are not
indexed here. Raw article SQL/presentation internals, transitive imports, HTTP
delivery and actual deployed environment also remain outside this slice.

Real production lineage, runtime traces and complete repository coverage remain
UNRESOLVED. Declared transport relationships are explained here; generated graph
arrows remain source-observed call syntax rather than invented cross-process
execution edges. Source tests protect the actual selected entrypoints, materializer
calls, frontier and exact spans without importing the API or accessing SQLite.

## Retained execution evidence

`python scripts/architecture_evidence.py --evidence <run-directory> --source-sha <exact-sha>`
projects an existing mutation report plus its actual baseline/mutant JUnit files.
It validates the complete declared family set, exact test bindings, case identity,
case counts and the named behavior assertions. A report's `KILLED` label alone is
not sufficient. The selected SHA is explicit: evidence from another revision is
`STALE`, not a current pass. This command does not execute tests or rewrite the
source index. Retained bytes are hashed; this is not a signed CI attestation or
independent proof of execution provenance.

The test-binding concept is reused from PR #324. Its declared `runtime_events`
must not produce `RUNTIME_OBSERVED` merely because a bound test passes. This
projection keeps runtime traces `UNKNOWN`; actual trace capture remains unfinished.
Current-source Explorer composition and three baseline-first historical mutation
contracts were merged and verified in #460. A mutation result proves only the
specific tested boundary, not complete production recovery or a release gate.

## Current-source Explorer

`/admin/architecture` reuses the existing #304/#328 Explorer, camera and mobile
panel controller. Its build projection uses this same index, not a second graph
declaration. Overview cards identify selected slices; they do not imply a flow.
Each slice displays selected roots plus one same-file candidate call hop. All
indexed symbols remain available through the lazy Code Structure panel; explicitly
scoped files are not presented as whole-file coverage.
Runtime state, operational criticality and semantic ownership remain UNKNOWN.
No production trace or old mutation PASS is bundled implicitly.

The browser receives sanitized symbol names, relative source spans and selected
test paths only. Raw SQL, native arguments and runtime documents stay out of the
bundle. Static JavaScript is not secret storage, even for an admin page. The
existing Access boundary is unchanged and still requires deployed acceptance.

The projection reuses React Flow 12.11.6 and Dagre 3.1.1, each MIT-licensed; exact
registry integrity is retained in `web/package-lock.json`. Official upstreams:
[React Flow](https://github.com/xyflow/xyflow) and
[Dagre](https://github.com/dagrejs/dagre). Existing transitive toolchain advisories
are not represented as resolved by adding these dependencies.

The existing required Python test shard runs actual source regeneration/check,
and the existing Web test command runs projection/mobile composition contracts.
The architecture workflow independently checks current source and retains real
historical mutation results. Its source check is required by the current rule.
Architecture, the existing Python owner shard and historical mutation jobs install
only the same locked parser when needed; their five-minute job limits are unchanged.

This increment advances #321's language/compiler intent and exposes another
current-source selection through the already-merged #304/#328 Explorer. #324's
runtime/test-evidence integration and the complete original intents remain partial;
neither this view nor a successful parser run authorizes closing those PRs.
The local API increment closes the missing entrypoint/materializer selection for
these News resources and exposes the Audit owner registration. It does not close
full repository classification, transitive ownership or runtime evidence work.

Graph publication runs after layout, outside React Flow's synchronous node
measurement cycle. Camera fitting still waits for initialized nodes and stable
frames. Mobile sheets preserve measured content width while locking page scroll
and restore the previous styles on close. These boundaries prevent a sheet from
re-entering ResizeObserver measurement; they are not runtime graph evidence.
