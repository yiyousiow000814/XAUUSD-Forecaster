import assert from "node:assert/strict";
import test from "node:test";

import {
  ADMIN_AUTH_COMPLETE_PATH,
  ADMIN_AUTH_MESSAGE_TYPE,
  ADMIN_SESSION_URL,
  adminAuthStateAfterProbe,
  isTrustedAdminAuthMessage,
  openAdminAuthPopup,
  probeAdminSession,
} from "../app/_lib/admin-auth-session.ts";

const response = ({
  status = 200, contentType = "application/json", body, redirected = false, type = "basic",
}) => ({
  status,
  ok: status >= 200 && status < 300,
  redirected,
  type,
  headers: new Headers({ "Content-Type": contentType }),
  json: async () => body,
});

test("uses the protected same-origin session response as the only authentication authority", async () => {
  let observed;
  const outcome = await probeAdminSession(async (url, options) => {
    observed = { url, options };
    return response({ body: { authenticated: true } });
  });
  assert.equal(outcome, "AUTHENTICATED");
  assert.equal(observed.url, ADMIN_SESSION_URL);
  assert.equal(observed.options.credentials, "same-origin");
  assert.equal(observed.options.cache, "no-store");
  assert.equal(observed.options.redirect, "manual");
});

test("separates expired sessions, forbidden identities, and transient failures", async () => {
  assert.equal(await probeAdminSession(async () => response({ status: 401 })), "ANONYMOUS");
  assert.equal(await probeAdminSession(async () => response({ status: 200, contentType: "text/html" })), "ANONYMOUS");
  assert.equal(await probeAdminSession(async () => response({ redirected: true })), "ANONYMOUS");
  assert.equal(await probeAdminSession(async () => response({
    status: 0, type: "opaqueredirect",
  })), "ANONYMOUS");
  assert.equal(await probeAdminSession(async () => response({ status: 403 })), "FORBIDDEN");
  assert.equal(await probeAdminSession(async () => response({ status: 503 })), "UNAVAILABLE");
  assert.equal(await probeAdminSession(async () => { throw new Error("offline"); }), "UNAVAILABLE");
  assert.equal(await probeAdminSession(async () => response({ body: { authenticated: false } })), "UNAVAILABLE");
  assert.equal(adminAuthStateAfterProbe("AUTHENTICATED", "UNAVAILABLE"), "AUTHENTICATED");
  assert.equal(adminAuthStateAfterProbe("AUTHENTICATED", "ANONYMOUS"), "ANONYMOUS");
  assert.equal(adminAuthStateAfterProbe("ANONYMOUS", "FORBIDDEN"), "FORBIDDEN");
});

test("popup messages require the expected origin, window, and message type", () => {
  const popup = {};
  const trusted = { origin: "https://example.test", source: popup, data: { type: ADMIN_AUTH_MESSAGE_TYPE } };
  assert.equal(isTrustedAdminAuthMessage(trusted, "https://example.test", popup), true);
  assert.equal(isTrustedAdminAuthMessage({ ...trusted, origin: "https://evil.test" }, "https://example.test", popup), false);
  assert.equal(isTrustedAdminAuthMessage({ ...trusted, source: {} }, "https://example.test", popup), false);
  assert.equal(isTrustedAdminAuthMessage({ ...trusted, data: { type: "authenticated" } }, "https://example.test", popup), false);
});

test("falls back to a full-page handoff when the browser blocks the popup", () => {
  let fallbackCalls = 0;
  let openedUrl;
  const popup = { focus() {} };
  assert.equal(openAdminAuthPopup((url) => {
    openedUrl = url;
    return popup;
  }, () => { fallbackCalls += 1; }), popup);
  assert.equal(openedUrl, ADMIN_AUTH_COMPLETE_PATH);
  assert.equal(fallbackCalls, 0);
  assert.equal(openAdminAuthPopup(() => null, () => { fallbackCalls += 1; }), null);
  assert.equal(fallbackCalls, 1);
});
