# Current-main dependency intent reconciliation

This bounded dependency change starts from main
`b962f4c51338424415d9e1d95bb36858d5194174`. It implements the eight retained
dependency intents, not the obsolete branches' complete lockfile snapshots.
None was already incorporated in that main. Closing an old PR still requires
merged implementation and exact-source validation; this document is not that
closure receipt.

| Original intent | Implemented target | Current validation owner |
|---|---|---|
| #272 React DOM types | `@types/react-dom@19.2.5` | TypeScript and complete Web build/tests |
| #273 framework beta | `vinext@1.0.0-beta.8` | Fixture producer/consumer, Web build/tests, branch Preview |
| #274 CSS integration | `@tailwindcss/postcss@4.3.3` | Native oxide, CSS build, rendered/responsive tests |
| #275 lint rules | `@next/eslint-plugin-next@16.3.3` | Complete lint and CI contracts |
| #276 Worker tooling | `wrangler@4.127.1` | Generated types, Worker validators, immutable build and Preview |
| #383 diagnostic uploader | `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (`v7.0.1`) | Existing Python, TLC and Windows diagnostic artifacts |
| #446 URI parser | Transitive `fast-uri@3.1.7` | Locked install, audit, schema/Worker tests |
| #450 browser targets | Transitive `browserslist@4.28.8` | Locked install, audit, actual JS/CSS build |

The same build graph's `fflate@0.7.4` finding is corrected to compatible
`0.7.5`, within `@shuding/opentype.js`'s `^0.7.3` range. It was already
reachable through vinext -> @vercel/og -> satori, independently of Explorer.
No broad audit fix, new direct dependency or dependency override was added.
Official exact npm metadata and the uploader tag were checked before resolving
with the repository's npm 11.6.2. The lock's integrity values remain authoritative
for clean installation; registry metadata alone is not runtime acceptance.

## Composition and reproducibility

The existing Cloudflare Vite plugin intentionally retains its exact Wrangler
4.123.0 dependency. The root CLI is 4.127.1. Both remain visible in the resolved
lock instead of forcing an unreviewed override. The root CLI's workerd types
were regenerated normally; `cf:types:check` is unchanged. Bindings and Worker
compatibility date did not change.

The first package-lock-only resolution omitted a dependency required by the
new Tailwind bundled WASM graph. A real installation completed that graph;
subsequent clean `npm ci` passed. No missing-package check was bypassed.
That original lock preserved bundled wasi-threads 1.2.2 and a separately
resolved 1.2.3. This describes the historical eight-target graph, not a permanent
requirement to retain both entries. A warm node_modules directory is not the gate.

The existing CI contract now checks direct exact package/lock agreement,
resolved integrity, one shared uploader identity across all three diagnostic
owners, seven-day retention, no hidden-file broadening, and unchanged five-minute
budgets. No required test or workflow was removed.

## Actual local evidence and remaining external gates

Windows Node 24.13.0 / npm 11.6.2:

- Clean locked install: 477 packages, 13.85 seconds.
- Complete `npm test`: Python fixture generation/verification, types check,
  actual build and 319 tests: 313 passed, zero failed, six existing local-only
  Preview-fixture skips; 17.71 seconds overall.
- Complete lint: zero errors, two newly reported Next navigation warnings in
  existing DashboardShell code; no rules suppressed and no unrelated UI rewrite.
- `npm audit --json`: zero reported vulnerabilities, including fast-uri,
  browserslist and fflate. This does not claim absence of unknown vulnerabilities.
- CI contracts and repository policy: 26 passed.

Exact-head GitHub checks and deployed branch Preview remain required. The six
tests requiring an embedded branch Preview cannot be represented as local passes.
For the uploader change, use the existing `python-*-result`, `tlc-*-result` and
`windows-runtime-*-result` outputs: after exact-head jobs upload them, download
the Linux and Windows artifacts read-only, check their existing result/source
fields and expected files, and reconcile them with the producing jobs. No new
artifact service, receipt database or duplicate CI platform is needed. Local
workflow inspection is not a successful provider upload/readback.

No change here installs Control Plane, mutates production, switches Stable,
activates Assistant, changes data schema, or authorizes closing unresolved PRs.

## Four later reviewed dependency deltas

The four later bot upgrades are separate from the eight frozen intentions above.
They were composed onto main `520ff05338bb576d2a05e664b4b967f9b98a37ac`, then
fast-forwarded to `72f56d56dca5a071368e619a279e231ff1fbca0a` without Web input changes or
replacing unrelated current-main package declarations or pursuing later versions.

| PR | Frozen reviewed head | Target |
|---|---|---|
| #272 | `6a621fd4e7e47b20f4ecfadcea726baab91a08a5` | `@types/react-dom@19.2.7` |
| #273 | `ded71986ac87945330ee11316cfac15d217d8c67` | `vinext@1.0.0-beta.9` |
| #275 | `3955bf01590a2982350a866eb465705490f0ec89` | `@next/eslint-plugin-next@16.3.4` |
| #276 | `022c6c4634e3bca694d0c6e9c2d35b4421ae05c4` | Root `wrangler@4.129.0` |

Complete frozen patches and exact final blobs were verified before composing
their semantic deltas. The combined lock changes 77 entries, including 61
peer-only entries. Root `@emnapi/runtime@1.11.3` serves Sharp's WASM path; bundled
oxide core/runtime 1.11.1 retain their bundled wasi-threads 1.2.2. Removed root
wasi-threads 1.2.3 has no surviving consumer in this graph. The root Wrangler
stack moves to Miniflare `5.20260903.0-alpha` and workerd `1.20260903.1`, including
its five matching platform packages. Vite-plugin-owned Wrangler 4.123.0 is
unchanged. No override or new direct dependency is introduced.

Initial Windows acceptance is **PARTIAL**, not a merged-intent closure:

- Clean npm 11.6.2 installation: 499 packages, 12.93 seconds, lock unchanged.
- Actual Wrangler type generation: exit 0, 3.42 seconds; existing compatibility
  date, bindings and flags unchanged. An external report printer then failed
  on its default Windows encoding; raw output/result were retained, and the
  printer was corrected without replaying type generation.
- Complete `npm test`: failed during actual beta.9 prerender after 12.94
  seconds, before the Node test suite. The lazy generated app entry referenced
  a cacheability manifest relative to its chunk directory, while the framework
  emitted that manifest at the server root. The existing public-shell publisher
  correctly rejected absent `/`, `/health`, and `/audit` HTML. This build is not
  a PASS; its failure record is retained separately from the corrected run below.
- Full lint: exit 0, three navigation warnings, no suppressed rules.
- An explicit TypeScript `--noEmit` check: exit 2, 93 diagnostics. Baseline versus
  dependency-induced attribution remains unresolved; no application semantics
  were changed to conceal diagnostics.
- Existing focused lock/CI and repository-policy checks: 14 passed.

These results bind to actual lock SHA-256
`dbfbe1a6bb404f8b364c34ca4d2c080dbdf8c081bc227def0ef4df0ed1927e94`.
Independent static graph review found no missing required dependency edge or new
unexplained semver conflict, but it cannot replace runtime acceptance. None of
these four PRs may be closed as implemented merely from the lock update or prior
eight-intent evidence.

### Corrected beta.9 packaging acceptance

The actual beta.9 emitter creates the cacheability module at the RSC output
root; its externalized relative import was consumed by a lazy entry chunk under
`_next/static`. The sibling client-assets module also has a late root update
after the earlier copy hook could run. The existing Vite prerender adapter now
resolves these two exact module imports relative to their chunk location instead
of copying root modules into chunk directories. The authoritative root producers,
module contents, public shell checks, and other imports remain unchanged.

The existing direct TypeScript dependency parses real module specifiers. The
family regressions cover root and nested paths, Windows path normalization,
wrong specifiers and ordinary string preservation, traversal rejection, missing
root failure, and a real ESM consumer observing a synthetic late root update.
This last fixture tests the ordering contract; it is not represented as an
execution of the upstream emitter. Independent review also checked the real
beta.9 emit/buildApp/prerender ordering and the actual initially failed chunk.

Corrected Windows `npm test` exited 0 in 13.249 seconds, with the lock unchanged:

- Actual generated-type check, framework/native CSS build and prerender passed.
- Ten routes were prerendered and ten HTML assets published, including all three
  mandatory `/`, `/health`, and `/audit` shells.
- The exact repository Python release fixture producer and Worker consumer ran.
- The Node suite reported 377 tests: 371 passed, zero failed, six existing local
  Preview-fixture skips. No skipped test is counted as passed.
- The actual nested entry imports `../../__vinext_cacheability_manifest.js` and
  `../../vinext-client-assets.js`; no duplicate chunk-directory modules exist.
- The focused build-environment family reported nine passed tests. The generated
  Worker types identify workerd `1.20260903.1`; the existing configuration hash,
  bindings, compatibility date, and flags did not change.

These are local working-source results on the recorded base, not an exact-head
remote gate or Cloudflare CPU result. Required exact-head Linux CI and deployed
Preview acceptance remain necessary, including the six embedded-Preview cases.
Focused lint of the final adapter, Vite config and test file passed in 2.620
seconds, including an explicit no-ignore check of the build helper. The separate
TypeScript comparison used a detached exact-main `72f56d56` source with a clean
installation of its complete old lock, not an incomplete warm dependency graph.
That install passed in 12.435 seconds. The identical `tsc --noEmit` command
reported 93 diagnostics in 5.906 seconds; old-lock and new-lock logs are
byte-identical. No added diagnostic was observed from these dependency deltas.
Both TypeScript runs remain failures, not converted to PASS; their existing
application findings are distinct from this repository's required npm-test gate.
