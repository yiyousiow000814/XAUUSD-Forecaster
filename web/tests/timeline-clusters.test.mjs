import assert from "node:assert/strict";
import test from "node:test";

const { clusterTimelineItems } = await import("../app/_lib/timeline-clusters.ts");

test("clusters colliding timeline targets without dropping audit events", () => {
  const events = [
    { id: "a", x: 0 },
    { id: "b", x: 30 },
    { id: "c", x: 55 },
    { id: "d", x: 110 },
  ];
  const groups = clusterTimelineItems(events, event => event.x, 44);

  assert.deepEqual(groups.map(group => group.map(event => event.id)), [
    ["a", "b", "c"],
    ["d"],
  ]);
  assert.deepEqual(groups.flat(), events, "presentation clustering must preserve every event");
  const centers = groups.map(group => group.reduce((total, event) => total + event.x, 0) / group.length);
  assert.ok(centers.every((center, index) => index === 0 || center - centers[index - 1] >= 44));
});

test("rejects an invalid interaction distance", () => {
  assert.throws(
    () => clusterTimelineItems([], () => 0, -1),
    /minimum distance must not be negative/,
  );
});
