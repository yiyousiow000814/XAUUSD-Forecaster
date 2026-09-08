import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const contract = JSON.parse(readFileSync(new URL("../cloudflare-build-contract.json", import.meta.url), "utf8"));
test("production builds use only main and native direct deployment", () => {
  assert.equal(contract.source.production_branch, "main");
  assert.equal(contract.non_production_builds_enabled, false);
  assert.equal(contract.commands.build, "npm ci && npm test");
  assert.equal(contract.commands.deploy, 'npx wrangler deploy --message "main:$WORKERS_CI_COMMIT_SHA"');
  assert.deepEqual(contract.output, {artifact_kind:"PRODUCTION_ARTIFACT", immutable_version_only:false, changes_stable_traffic:true});
});
