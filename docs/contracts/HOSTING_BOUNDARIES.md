# Hosting Boundaries Contract

## Public boundary

- Cloudflare Workers is the only deployment plane for this repository.
- GitHub is a source-control and validation plane only. Repository automation
  MUST NOT create GitHub Deployments or GitHub Environments, and pull requests
  MUST NOT request deployment to a GitHub environment.
- Public visitors read remote dashboard state and never connect to localhost.
- Public research API reads may be anonymous, but model-consuming Assistant
  routes are private under
  [`ASSISTANT_SECURITY.md`](ASSISTANT_SECURITY.md). Every ingest route requires
  the Worker secret `INGEST_TOKEN`.
- Local secrets remain outside Git and must not appear in public responses or
  logs.
- The ChatGPT Sites bypass header may be sent only to `*.chatgpt.site`; it must
  never be forwarded to Cloudflare or another target.

## Repository enforcement boundary

- `Repository policy` is the authoritative merge-time check for this hosting
  boundary. It reads a pull request with the checker from protected `main`; it
  MUST NOT execute code from the candidate branch.
- The check rejects GitHub Actions environments, `deployments: write`, and
  repository automation that invokes GitHub Deployments or Environments APIs.
  Cloudflare Workers builds, Git integration, and branch Previews remain
  allowed.
- The `Repository policy` check MUST be required by the `main` branch ruleset.
  With that external setting, a failing or missing check prevents acceptance
  of forbidden hosting architecture into protected `main`.
- Repository-local Actions are not a pre-execution firewall. GitHub may parse
  or begin another workflow from a pull request before this check completes.
  This contract guarantees merge-time repository enforcement, not universal
  prevention of every attempted workflow execution.

## Target isolation

- Each hosting target has independent synchronization state and health.
- Failure of one target must not stop synchronization to the other target.
- If both targets reject the heartbeat, synchronization must expose an error.
- Public-hosting failure must not stop local evidence collection.
- Optional growing resources, such as news details, must not mark an otherwise
  current live heartbeat offline.

## D1 capability readiness

- Deployed code declares named D1 capabilities as bounded sets of required
  schema objects. Runtime and deployment-readiness probes must fail with an
  explicit `D1_SCHEMA_CAPABILITY_MISSING` error and the missing capability and
  table names when production D1 does not satisfy that declaration.
- Capability checks do not execute migrations. Migration files and the
  reviewed Wrangler migration command remain the controlled schema authority.
- Capability names describe product behavior rather than a permanent migration
  number, so later additive migrations can extend or supersede the required
  object set without embedding one incident's filenames in runtime logic.

## Cross-boundary growth and critical paths

- Before data crosses a process, service, hosting, storage, synchronization, or
  API boundary, its owner and source of truth must be identified, the path must
  be classified as critical or optional, and its work and transport growth must
  be classified against accumulated state.
- A path that communicates liveness, readiness, current authority, deployment,
  or control state must have bounded work and bounded transport independently
  of history, record, user, retry, or generation growth. Its representation is
  an explicit projection of current state and bounded summaries; newly added
  data does not enter that path merely because it appears in a shared source
  object.
- Growing state crosses the boundary through an independently bounded resource,
  such as a cursor page, byte-bounded batch, indexed D1 ledger, or lazy read.
  One operation must remain bounded as total authoritative state grows, and a
  complete source of truth must remain reachable without creating another
  full-history blob.
- Provider-capacity status crosses the dashboard boundary only as bounded,
  secret-safe per-authority/account projections. Forecasting may use a retained
  quota-day summary, but critical status must never scan accumulated provider
  requests or model attempts. Historical contract migration is preemptible and
  may consume only forecast-safe surplus provider capacity; its latency remains
  separate from LIVE pipeline health.
- A display limit or business selection window is not a transport guarantee.
  Transport bounds are enforced on serialized bytes at the transport boundary,
  with enough normal headroom that the emergency host limit remains a final
  guard rather than the storage model.
- Failure belongs to the resource whose write or read failed. Optional or
  growing-resource failure remains visible as that resource's degraded state,
  but shared plumbing must still publish unrelated healthy critical state.
- When a bound is exceeded, repair ownership, projection, pagination, batching,
  or failure isolation first. Do not default to raising the host limit or
  deleting authoritative evidence.

Preview-specific write isolation and provenance guarantees are defined in
[`PREVIEW_ISOLATION.md`](PREVIEW_ISOLATION.md).
