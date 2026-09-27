# XAUUSD News Rename Plan

## Change and invariants

Rename the repository, Python distribution and import package, Windows runtime
and scheduled owner, and Cloudflare Worker to `xauusd-news` (`xauusd_news` for
Python imports). The public product keeps its existing Chinese name. The Worker
hostname changes with the Worker name. Preserve repository and Worker identities,
D1 database UUID, schemas, credential identities, source receipts, all news and
quote records, Access policies, strict ACK, and the single main runtime owner.
No model or Assistant activation is included.

## Ownership and reachable states

GitHub protected main owns deployed source. Native Workers Builds owns production
activation. The local scheduled main owner owns collector, annotation, quote,
API, and sync processes. Mutable files belong to the runtime root; configuration
and secrets belong to the repository root. Git owns shared worktree metadata.

Imports, package data, entrypoint bootstrap paths, source explorers, validators,
rehearsal boundaries, and docs must move together. Generate architecture evidence
from the renamed source rather than modifying generated digests by replacement.
The credential HMAC domain is a persisted protocol identifier, not branding; its
bytes must remain stable so the rename cannot reset quota or credential state.
Historical receipts and immutable Preview URLs remain factual historical data.

## Rollout and recovery

1. Prepare and test the renamed source without touching the active runtime.
2. Verify Cloudflare rename, Builds, and Access authority before switching URLs.
   Keep the Worker UUID, D1 UUID, bindings, secrets and existing policies intact.
3. Export the existing task definition and baseline service/data identities.
   Ask the old owner to stop, disable its exact scheduled task, and wait for all
   owned business processes to exit. Then stop that exact idle owner/launcher.
4. Move the stopped runtime directory on the same volume; repair Git worktree
   pointers, resolve mutable path settings, install the renamed package, and
   register one new task. Do not rewrite immutable historical receipts.
5. Activate only accepted main code. Verify one new owner, no old writer, preserved
   SQLite state and records, progressing collection and successful strict ACK.
6. Remove transition aliases only after their consumers have moved. A locked
   development root is an explicit incomplete migration, not a successful rename.

On failure before takeover, leave the old runtime running. During relocation,
retain stopped intent and the saved exact task/data state until the new root is
usable. Never launch two owners against the same data through path aliases.
Provider permission failures block that cutover; they do not justify weakening
Access or deploying a branch to production. Recovery fixes forward through main.

## Acceptance and review

Run Python package/import/entrypoint tests from an unrelated cwd, Windows owner
and containment contracts, repository policy, regenerated architecture checks,
full Python and Web regressions, and a wheel installation rehearsal. Verify the
renamed deployed Preview on desktop and both phone viewports for changed links.
Review actual entrypoint arguments, absolute path readers, environment variables,
task actions, Worker bindings, source links and latest production identities.
Record actual post-cutover evidence and remaining blockers before completion.

## Provider limits

D1 exposes no database-name update in its API or dashboard. Keep the existing
`aurum-signal-room` database name and UUID; a branding change is not authority to
replace the database. The Worker and public hostname can be renamed in place.
