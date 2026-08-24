import { env } from "cloudflare:workers";

const digest = async (value: string) => new Uint8Array(
  await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
);

let cachedExpectedToken: string | undefined;
let cachedExpectedDigest: Promise<Uint8Array> | undefined;

function expectedTokenDigest(expected: string): Promise<Uint8Array> {
  if (cachedExpectedToken !== expected || !cachedExpectedDigest) {
    cachedExpectedToken = expected;
    cachedExpectedDigest = digest(expected);
  }
  return cachedExpectedDigest;
}

export async function isIngestAuthorized(request: Request): Promise<boolean> {
  const expected = env.INGEST_TOKEN ?? process.env.INGEST_TOKEN;
  const header = request.headers.get("authorization") ?? "";
  const match = /^Bearer\s+(.+)$/i.exec(header);
  if (!expected || !match?.[1]) return false;

  const [expectedDigest, providedDigest] = await Promise.all([
    expectedTokenDigest(expected),
    digest(match[1]),
  ]);
  let difference = expectedDigest.length ^ providedDigest.length;
  for (let index = 0; index < expectedDigest.length; index += 1) {
    difference |= expectedDigest[index] ^ providedDigest[index];
  }
  return difference === 0;
}
