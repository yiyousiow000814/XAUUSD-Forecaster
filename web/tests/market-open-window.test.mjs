import assert from "node:assert/strict";
import test from "node:test";

const { buildMarketOpenResultWindows, marketOpenElapsedMs } = await import(
  "../app/_lib/market-open-window.ts"
);

const HOUR_MS = 3_600_000;

test("excludes only the expected New York weekly closure", () => {
  assert.equal(
    marketOpenElapsedMs(
      Date.parse("2026-08-14T20:00:00Z"),
      Date.parse("2026-08-17T02:00:00Z"),
    ),
    5 * HOUR_MS,
  );
  assert.equal(
    marketOpenElapsedMs(
      Date.parse("2026-08-17T00:00:00Z"),
      Date.parse("2026-08-17T03:00:00Z"),
    ),
    3 * HOUR_MS,
    "an unexplained open-session gap must still consume the window",
  );
});

test("keeps weekly closure exact across both New York DST transitions", () => {
  assert.equal(
    marketOpenElapsedMs(
      Date.parse("2026-03-06T22:00:00Z"),
      Date.parse("2026-03-08T22:00:00Z"),
    ),
    0,
  );
  assert.equal(
    marketOpenElapsedMs(
      Date.parse("2026-10-30T21:00:00Z"),
      Date.parse("2026-11-01T23:00:00Z"),
    ),
    0,
  );
});

test("carries a 24-hour OOS window backward across the weekend", () => {
  const windows = buildMarketOpenResultWindows([
    Date.parse("2026-08-13T23:00:00Z"),
    Date.parse("2026-08-14T01:00:00Z"),
    Date.parse("2026-08-14T20:00:00Z"),
    Date.parse("2026-08-16T22:00:00Z"),
    Date.parse("2026-08-17T02:00:00Z"),
  ], 24 * HOUR_MS);
  assert.deepEqual(windows[0], {
    start: Date.parse("2026-08-14T01:00:00Z"),
    end: Date.parse("2026-08-17T02:00:00Z"),
  });
});
