export function clusterTimelineItems<T>(
  items: T[],
  position: (item: T) => number,
  minimumDistance: number,
): T[][] {
  if (!Number.isFinite(minimumDistance) || minimumDistance < 0) {
    throw new RangeError("minimum distance must not be negative");
  }
  return items.reduce<T[][]>((groups, item) => {
    const previous = groups.at(-1);
    if (!previous) return [[item]];
    const center = previous.reduce((total, entry) => total + position(entry), 0) / previous.length;
    if (Math.abs(position(item) - center) < minimumDistance) previous.push(item);
    else groups.push([item]);
    return groups;
  }, []);
}
