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
The lock preserves both bundled wasi-threads 1.2.2 and the separately resolved
1.2.3 required by the graph. A warm node_modules directory is not the gate.

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
