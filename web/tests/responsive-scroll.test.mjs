import assert from "node:assert/strict";
import test from "node:test";

import { settleResponsiveScroll } from "../app/_lib/responsive-scroll.ts";

test("cancels an unfinished desktop scroll restoration", () => {
  const originalWindow = globalThis.window;
  const frames = new Map();
  let nextFrame = 1;
  globalThis.window = {
    matchMedia: () => ({ matches: false }),
    requestAnimationFrame(callback) {
      const frame = nextFrame++;
      frames.set(frame, callback);
      return frame;
    },
    cancelAnimationFrame(frame) {
      frames.delete(frame);
    },
  };

  try {
    const writes = [];
    const cancel = settleResponsiveScroll(options => writes.push(options), () => 0, 480);
    const first = frames.values().next().value;
    frames.clear();
    first();
    assert.equal(writes.length, 1);
    assert.equal(frames.size, 1);

    const stale = frames.values().next().value;
    cancel();
    assert.equal(frames.size, 0);
    stale();
    assert.equal(writes.length, 1);
  } finally {
    globalThis.window = originalWindow;
  }
});

test("resets phones immediately without scheduling restoration frames", () => {
  const originalWindow = globalThis.window;
  let scheduled = 0;
  globalThis.window = {
    matchMedia: () => ({ matches: true }),
    requestAnimationFrame() {
      scheduled += 1;
      return scheduled;
    },
    cancelAnimationFrame() {},
  };

  try {
    const writes = [];
    const cancel = settleResponsiveScroll(options => writes.push(options), () => 75, 75);
    assert.deepEqual(writes, [{ top: 0, left: 0, behavior: "instant" }]);
    assert.equal(scheduled, 0);
    assert.doesNotThrow(cancel);
  } finally {
    globalThis.window = originalWindow;
  }
});
