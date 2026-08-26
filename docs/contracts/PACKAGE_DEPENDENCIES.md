# Canonical Package Dependencies Contract

## Scope and current state

This contract governs Python source dependencies during and after the
`PENDING` modularization campaign. It is derived from the audited
`d4103fbe61e0c025b9d246d35804fecb2a3c3fdb` graph rather than an idealized
green-field layout.

The audited campaign base had a flat root package, one 14-module SCC, no
package import from `scripts`, and three script-to-script shared-library call
sites. Canonical owner moves preserve those facts as baseline evidence rather
than describing the pending stack as already merged architecture.

## Enforced rules

1. Code under `xauusd_forecaster/` MUST NOT import `scripts`.
2. Runtime entry-point scripts MUST NOT be shared libraries. During the stack,
   only `build_preview_bundle.py` and `build_release_validation_fixtures.py`
   may temporarily import `run_dashboard_sync.py`; C4 removes both exceptions.
3. Nested package `__init__.py` files may contain only a docstring, explicit
   imports, `__all__`, and narrowly documented aliases. They must not construct
   clients, open stores, start threads, or install schemas.
4. A canonical module MUST NOT import a legacy path marked `THIN_SHIM` in the
   Module Migration Map.
5. Stable entry scripts may import package owners. Package owners may never
   import an entry script.

## Canonical direction

Flat modules are classified during migration. For canonical packages, allowed
dependencies are:

| Canonical owner | May depend on |
|---|---|
| `ai` | foundational contracts |
| `evidence` | foundational contracts, `ai` |
| `news` | foundational contracts, `ai`, `evidence` |
| `training` | foundational contracts, `ai`, `evidence`, `news` |
| `decision` | foundational contracts, `ai`, `evidence`, `news`, `training` |
| `runtime` | foundational contracts |
| `assistant` | foundational contracts, `ai`, `evidence`, `news` |
| `dashboard` | foundational contracts and all read/projection inputs |

“Foundational contracts” means deliberately retained root models, value
objects, public facades, and cross-cutting schemas documented in the Codebase
Map. It does not mean arbitrary flat business logic.

The following reverse edges are prohibited regardless of migration status:

- Decision, Training, News, Evidence, AI, Runtime, and Assistant may not import
  Dashboard.
- Decision may not import Cloudflare/Web implementation.
- Training may not import Dashboard.
- The five-minute Decision owner may not import Assistant.
- Canonical code may not import a legacy compatibility shim.

Dashboard is a terminal read/projection layer. Runtime entry points orchestrate
owners but are outside the canonical dependency graph. Assistant remains
separately bounded and is not a forecasting critical-path dependency.

## Shared-store bootstrap component

After canonical News/AI placement, the import inventory contains one 13-module
component, reduced from the 14-module baseline. It is explicitly retained as a
co-resident SQLite schema-bootstrap contract: `evidence.ledger` installs the
News scheduler/semantic and retained Assistant schemas, while those owners use
the same ledger type. Market/session contracts also participate through the
point-in-time news clock. The imports are local at schema-install or transition
boundaries and do not create a second module instance, process, store, or
runtime owner. Removing this true shared-store bootstrap component requires a
separate schema-composition boundary and is not safe to disguise as an import
move. No Dashboard, Training, Decision runtime, Cloudflare, or entry-point
module participates in the component.

## Compatibility handover

Compatibility shims contain no business logic, SQL, mutable singleton,
provider client, schema installation, or state transition. Each shim requires
an owner and removal condition in
`docs/reference/MODULE_MIGRATION_MAP.md`. The architecture checker enforces
these restrictions from the checked-out source.
