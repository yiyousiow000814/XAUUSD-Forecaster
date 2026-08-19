import assert from "node:assert/strict";
import test from "node:test";

import {
  ADMIN_RELOGIN_MESSAGE,
  adminErrorPresentation,
  adminResponseError,
} from "../app/_lib/admin-client.ts";
import { assistantHealthPresentation } from "../app/_lib/assistant-health-presentation.ts";

test("presents authoritative Assistant health compactly", () => {
  assert.equal(assistantHealthPresentation({ status: "HEALTHY", current: true }), "运行正常");
  assert.equal(assistantHealthPresentation(null), "运行状态暂不可用");
  assert.equal(assistantHealthPresentation({
    status: "ERROR", current: true,
    alerts: [{ message_zh: "Assistant 对话队列阻塞。", blocking: true }],
  }), "Assistant 对话队列阻塞。");
  assert.equal(assistantHealthPresentation({ status: "HEALTHY", current: false }), "运行正常（合成）");
});

test("separates expired Admin sessions from service failures", () => {
  const auth = adminErrorPresentation(
    adminResponseError(new Response(null, { status: 401 }), "unavailable"),
    "状态暂不可用",
  );
  assert.deepEqual(auth, { kind: "AUTH_REQUIRED", message: ADMIN_RELOGIN_MESSAGE });
  const accessHtml = adminErrorPresentation(adminResponseError(new Response("login", {
    status: 200, headers: { "Content-Type": "text/html" },
  }), "unavailable"), "状态暂不可用");
  assert.deepEqual(accessHtml, { kind: "AUTH_REQUIRED", message: ADMIN_RELOGIN_MESSAGE });

  const unavailable = adminErrorPresentation(
    adminResponseError(new Response(null, { status: 503 }), "状态暂不可用"),
    "状态暂不可用",
  );
  assert.deepEqual(unavailable, { kind: "UNAVAILABLE", message: "状态暂不可用" });
  assert.notEqual(unavailable.message, ADMIN_RELOGIN_MESSAGE);
  const htmlFailure = adminErrorPresentation(adminResponseError(new Response("failure", {
    status: 503, headers: { "Content-Type": "text/html" },
  }), "状态暂不可用"), "状态暂不可用");
  assert.equal(htmlFailure.kind, "UNAVAILABLE");
});
