import assert from "node:assert/strict";
import test from "node:test";

import { exportJWK, generateKeyPair, SignJWT } from "jose";
import {
  authenticateDashboardOperatorRequest,
  dashboardOperatorAuthFailure,
  dashboardOperatorSessionResponse,
} from "../app/api/_shared/dashboard-operator-auth.ts";

const runtimeEnv = {
  CF_ACCESS_TEAM_DOMAIN: "aurum.cloudflareaccess.com",
  CF_ACCESS_AUD: "assistant-audience",
  DASHBOARD_OPERATOR_OWNER_SUBJECTS: "owner-subject",
};

const request = token => new Request("https://example.test/api/news-questions", {
  headers: token ? { "Cf-Access-Jwt-Assertion": token } : {},
});

async function signedToken(overrides = {}) {
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  const jwk = await exportJWK(publicKey);
  Object.assign(jwk, { alg: "RS256", kid: "test-key", use: "sig" });
  const token = await new SignJWT({
    email: "owner@example.com",
    type: "app",
    ...overrides.claims,
  })
    .setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setSubject(overrides.subject ?? "owner-subject")
    .setIssuer(overrides.issuer ?? "https://aurum.cloudflareaccess.com")
    .setAudience(overrides.audience ?? "assistant-audience")
    .setIssuedAt()
    .setExpirationTime("5m")
    .sign(privateKey);
  return { token, jwk };
}

test("verifies the Access signature, issuer, audience, user type, and owner membership", async () => {
  const signed = await signedToken();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async url => {
    assert.equal(String(url), "https://aurum.cloudflareaccess.com/cdn-cgi/access/certs");
    return Response.json({ keys: [signed.jwk] });
  };
  try {
    assert.deepEqual(
      await authenticateDashboardOperatorRequest(request(signed.token), runtimeEnv),
      {
        state: "AUTHORIZED",
        actor: { actor_id: "cloudflare-access:owner-subject", role: "OWNER" },
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("does not trust a header without a valid signed token", async () => {
  let verifierCalls = 0;
  assert.deepEqual(await authenticateDashboardOperatorRequest(
    request("not-a-jwt"),
    runtimeEnv,
    async () => {
      verifierCalls += 1;
      throw new Error("invalid signature");
    },
  ), { state: "AUTH_REQUIRED" });
  assert.equal(verifierCalls, 1);
  assert.deepEqual(
    await authenticateDashboardOperatorRequest(request(null), runtimeEnv),
    { state: "AUTH_REQUIRED" },
  );
});

test("fails closed for service identities, strangers, and missing owner configuration", async () => {
  const verify = async () => ({
    sub: "stranger-subject",
    email: "stranger@example.com",
    type: "app",
  });
  assert.deepEqual(
    await authenticateDashboardOperatorRequest(request("token"), runtimeEnv, verify),
    { state: "FORBIDDEN" },
  );
  assert.deepEqual(await authenticateDashboardOperatorRequest(
    request("token"),
    runtimeEnv,
    async () => ({ sub: "", email: "owner@example.com", type: "app" }),
  ), { state: "AUTH_REQUIRED" });
  assert.deepEqual(await authenticateDashboardOperatorRequest(
    request("token"),
    { ...runtimeEnv, DASHBOARD_OPERATOR_OWNER_SUBJECTS: "" },
    async () => ({ sub: "owner-subject", email: "owner@example.com", type: "app" }),
  ), { state: "UNAVAILABLE" });
});

test("email may authorize membership but never becomes actor identity", async () => {
  const actor = await authenticateDashboardOperatorRequest(
    request("token"),
    {
      ...runtimeEnv,
      DASHBOARD_OPERATOR_OWNER_SUBJECTS: "",
      DASHBOARD_OPERATOR_OWNER_EMAILS: "OWNER@EXAMPLE.COM",
    },
    async () => ({ sub: "stable-subject", email: "owner@example.com", type: "app" }),
  );
  assert.deepEqual(actor, {
    state: "AUTHORIZED",
    actor: { actor_id: "cloudflare-access:stable-subject", role: "OWNER" },
  });
});

test("shared operator allowlist takes precedence over legacy Assistant cutover values", async () => {
  const verify = async () => ({
    sub: "legacy-owner", email: "legacy@example.com", type: "app",
  });
  assert.deepEqual(await authenticateDashboardOperatorRequest(request("token"), {
    ...runtimeEnv,
    DASHBOARD_OPERATOR_OWNER_SUBJECTS: "current-owner",
    ASSISTANT_OWNER_SUBJECTS: "legacy-owner",
  }, verify), { state: "FORBIDDEN" });

  assert.deepEqual(await authenticateDashboardOperatorRequest(request("token"), {
    ...runtimeEnv,
    DASHBOARD_OPERATOR_OWNER_SUBJECTS: undefined,
    ASSISTANT_OWNER_SUBJECTS: "legacy-owner",
  }, verify), {
    state: "AUTHORIZED",
    actor: { actor_id: "cloudflare-access:legacy-owner", role: "OWNER" },
  });
});

test("maps shared authentication states to fail-closed HTTP contracts", async () => {
  const required = dashboardOperatorAuthFailure({ state: "AUTH_REQUIRED" });
  assert.equal(required.status, 401);
  assert.equal((await required.json()).code, "DASHBOARD_OPERATOR_AUTH_REQUIRED");

  const forbidden = dashboardOperatorAuthFailure({ state: "FORBIDDEN" });
  assert.equal(forbidden.status, 403);
  assert.equal((await forbidden.json()).code, "DASHBOARD_OPERATOR_FORBIDDEN");

  const unavailable = dashboardOperatorAuthFailure({ state: "UNAVAILABLE" });
  assert.equal(unavailable.status, 503);
  assert.equal((await unavailable.json()).code, "DASHBOARD_OPERATOR_AUTH_UNAVAILABLE");

  const session = dashboardOperatorSessionResponse({
    state: "AUTHORIZED",
    actor: { actor_id: "cloudflare-access:owner-subject", role: "OWNER" },
  });
  assert.equal(session.status, 200);
  assert.deepEqual(await session.json(), { authenticated: true });
  assert.match(session.headers.get("cache-control"), /no-store/);
  assert.equal(dashboardOperatorSessionResponse({ state: "AUTH_REQUIRED" }).status, 401);
});
