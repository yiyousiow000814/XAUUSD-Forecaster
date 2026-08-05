/**
 * Advance the locally measured quote age by the mirror transport delay.
 * Five-minute decision timestamps are intentionally not used as quote heartbeats.
 *
 * @param {{
 *   generated_at?: string,
 *   system?: { online?: boolean, quote_age_seconds?: number | null }
 * }} payload
 * @param {number} nowMilliseconds
 */
export function applyFreshness(payload, nowMilliseconds = Date.now()) {
  const generated = Date.parse(payload.generated_at ?? "");
  const relayAge = Number.isFinite(generated)
    ? Math.max(0, (nowMilliseconds - generated) / 1000)
    : null;
  const reportedAge = payload.system?.quote_age_seconds;
  const age =
    relayAge !== null && typeof reportedAge === "number" && Number.isFinite(reportedAge)
      ? Math.max(0, reportedAge) + relayAge
      : null;
  return {
    ...payload,
    system: {
      ...payload.system,
      quote_age_seconds: age,
      online: payload.system?.online === true && age !== null && age <= 75,
    },
  };
}
