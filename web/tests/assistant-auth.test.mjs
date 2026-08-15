import assert from "node:assert/strict";
import test from "node:test";

import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { authenticateAssistantRequest } from "../app/api/_shared/assistant-auth.ts";

const runtimeEnv = {
  CF_ACCESS_TEAM_DOMAIN: "aurum.cloudflareaccess.com",
  CF_ACCESS_AUD: "assistant-audience",
  ASSISTANT_OWNER_SUBJECTS: "owner-subject",
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
      await authenticateAssistantRequest(request(signed.token), runtimeEnv),
      { actor_id: "cloudflare-access:owner-subject", role: "OWNER" },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("does not trust a header without a valid signed token", async () => {
  let verifierCalls = 0;
  assert.equal(await authenticateAssistantRequest(
    request("not-a-jwt"),
    runtimeEnv,
    async () => {
      verifierCalls += 1;
      throw new Error("invalid signature");
    },
  ), null);
  assert.equal(verifierCalls, 1);
  assert.equal(await authenticateAssistantRequest(request(null), runtimeEnv), null);
});

test("fails closed for service identities, strangers, and missing owner configuration", async () => {
  const verify = async () => ({
    sub: "stranger-subject",
    email: "stranger@example.com",
    type: "app",
  });
  assert.equal(await authenticateAssistantRequest(request("token"), runtimeEnv, verify), null);
  assert.equal(await authenticateAssistantRequest(
    request("token"),
    runtimeEnv,
    async () => ({ sub: "", email: "owner@example.com", type: "app" }),
  ), null);
  assert.equal(await authenticateAssistantRequest(
    request("token"),
    { ...runtimeEnv, ASSISTANT_OWNER_SUBJECTS: "" },
    async () => ({ sub: "owner-subject", email: "owner@example.com", type: "app" }),
  ), null);
});

test("email may authorize membership but never becomes actor identity", async () => {
  const actor = await authenticateAssistantRequest(
    request("token"),
    {
      ...runtimeEnv,
      ASSISTANT_OWNER_SUBJECTS: "",
      ASSISTANT_OWNER_EMAILS: "OWNER@EXAMPLE.COM",
    },
    async () => ({ sub: "stable-subject", email: "owner@example.com", type: "app" }),
  );
  assert.deepEqual(actor, {
    actor_id: "cloudflare-access:stable-subject",
    role: "OWNER",
  });
});
