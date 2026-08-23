# Repository Modularization Execution

## Full-stack latest-main integration (PENDING)

This section records a pending Draft PR stack reconstruction. It does not
describe merged `CURRENT` architecture.

### Architecture change gate

```text
Owner: Repository modularization campaign; each PR retains one named owner boundary.
Authoritative state/store: Git commits, Draft PR metadata, architecture contracts, and test inventories.
Execution boundary: Source/build/test only; no runtime, provider, database, deployment, or Stable mutation.
Critical or optional: Optional development and validation path.
Maximum work per operation: One branch rebase and its bounded focused/full validation before proceeding.
Incremental cursor/revision/checkpoint: Exact parent/head SHA, normalized pytest node inventory, and exact-head CI check set.
Failure domain: The lowest PR that owns a semantic conflict or missing contract.
Last-good/recovery behavior: Preserve remote pre-integration heads; rewrite only with force-with-lease after local proof.
Architecture documents affected: SYSTEM_ARCHITECTURE, RUNTIME_AND_RELEASE, CODEBASE_MAP, this tracker, and final closure audit.
```

### Pre-integration snapshot

- Verification date: 2026-08-24.
- Latest main: `17b71467d95dbde12ed59cda2537a4e4b5f32a1e`.
- Rebuild reason: the pending modularization stack predates the Control Plane,
  bounded repository retry, exact child identity/WPF lifecycle, and deterministic
  structured Control Center operation-result contracts merged by #284, #286,
  #293, and #300.
- Latest-main collection: 1,547 tests.
- Pre-rebase Closure collection: 1,512 tests.
- Assistant remains `PAUSED`.

| PR | Branch | Pre-integration head |
|---|---|---|
| #282 | `docs/architecture-baseline` | `5fb64cbb05f7487f59bcc8a8ec779c4bd9fd8f44` |
| #283 | `refactor/dashboard-status-cache` | `9dfdbf606211b8ac836451d84b6f3ede95418779` |
| #285 | `refactor/dashboard-health-projection` | `d4103fbe61e0c025b9d246d35804fecb2a3c3fdb` |
| #287 | `refactor/dashboard-resource-contracts` | `8bad9dca33099bb53b13bf1b4089c7e2d09dea72` |
| #288 | `refactor/dashboard-api-news-resources` | `ffc38d8786d73b0e1b963b35b3e497e0fcae7604` |
| #289 | `refactor/dashboard-api-market-resources` | `db8f10d8ad4334a7d14351ced1566223025bedcf` |
| #290 | `refactor/dashboard-api-optional-resources` | `ada781248cc18723a2b11ce563c135c67e9656d7` |
| #291 | `refactor/dashboard-operator-bridge` | `6358b897f88a3c8bdf8de2af63421fa65dc11312` |
| #292 | `refactor/dashboard-sync-runtime` | `305eb1113026f974aee12ce0ca8bbe475a3eebdf` |
| #294 | `refactor/news-annotator-runtime` | `caf0a8a0862cceb939159faa39c49dd9dea7ef97` |
| #295 | `refactor/control-center-boundaries` | `a3dd64fee0b58967475d3c36f9841986162bccb9` |
| #296 | `refactor/decision-evidence-packages` | `a73b594893dd68bf1e87d89a89ac60ad6161ad56` |
| #297 | `refactor/training-package` | `304e44cac64cf50be6ef319caffbc5dd90db24a5` |
| #298 | `refactor/news-ai-packages` | `388b6a870693b6132324fbfa4ca013975545f051` |
| #299 | `refactor/assistant-runtime-dashboard-packages` | `bd9aa3626da5bf3cb4a44883f64c4d6e14944dff` |
| #301 | `refactor/test-organization` | `2481d3170ba797cd0d5e5aafefd0b26d9490e8fe` |
| #302 | `chore/modularization-campaign-closure` | `c8b1ed04152064c979ef188a891ba2be7166d2a9` |

Replacement heads and semantic conflict ownership are recorded as each Draft
PR is repaired. A replacement head remains `PENDING` until its PR is merged.
