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

Preview-specific write isolation and provenance guarantees are defined in
[`PREVIEW_ISOLATION.md`](PREVIEW_ISOLATION.md).
