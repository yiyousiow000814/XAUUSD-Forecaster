import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";

// Human Dashboard Operator authorization is independent from machine ingest.

export type DashboardOperatorActor = {
  actor_id: string;
  role: "OWNER";
};

type AccessClaims = JWTPayload & {
  email?: unknown;
  type?: unknown;
};

type AccessConfig = {
  issuer: string;
  audience: string[];
};

type AccessVerifier = (
  token: string,
  config: AccessConfig,
) => Promise<AccessClaims>;

const jwksByIssuer = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

const csv = (value: string | undefined) => (value ?? "")
  .split(",")
  .map(item => item.trim())
  .filter(Boolean);

const accessConfig = (runtimeEnv: Cloudflare.Env) => {
  const domain = runtimeEnv.CF_ACCESS_TEAM_DOMAIN?.trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/+$/, "");
  const audience = csv(runtimeEnv.CF_ACCESS_AUD);
  if (!domain || audience.length === 0) return null;
  return { issuer: `https://${domain}`, audience };
};

const ownerAllowlist = (runtimeEnv: Cloudflare.Env) => {
  const configuredSubjects = csv(runtimeEnv.DASHBOARD_OPERATOR_OWNER_SUBJECTS);
  const configuredEmails = csv(runtimeEnv.DASHBOARD_OPERATOR_OWNER_EMAILS);
  // Preserve a safe cutover path for existing deployments. Once either shared
  // allowlist is configured, legacy Assistant values cannot broaden it.
  const subjects = configuredSubjects.length || configuredEmails.length
    ? configuredSubjects : csv(runtimeEnv.ASSISTANT_OWNER_SUBJECTS);
  const emails = configuredSubjects.length || configuredEmails.length
    ? configuredEmails : csv(runtimeEnv.ASSISTANT_OWNER_EMAILS);
  return {
    subjects: new Set(subjects),
    emails: new Set(emails.map(value => value.toLocaleLowerCase("en-US"))),
  };
};

const defaultVerifier: AccessVerifier = async (token, config) => {
  let jwks = jwksByIssuer.get(config.issuer);
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(`${config.issuer}/cdn-cgi/access/certs`));
    jwksByIssuer.set(config.issuer, jwks);
  }
  const verified = await jwtVerify(token, jwks, {
    algorithms: ["RS256"],
    audience: config.audience,
    issuer: config.issuer,
    clockTolerance: 5,
  });
  return verified.payload as AccessClaims;
};

export async function authenticateDashboardOperatorRequest(
  request: Request,
  runtimeEnv: Cloudflare.Env,
  verify: AccessVerifier = defaultVerifier,
): Promise<DashboardOperatorActor | null> {
  const config = accessConfig(runtimeEnv);
  const token = request.headers.get("cf-access-jwt-assertion")?.trim();
  if (!config || !token || token.length > 8_192) return null;

  try {
    const claims = await verify(token, config);
    const subject = typeof claims.sub === "string" ? claims.sub.trim() : "";
    const email = typeof claims.email === "string"
      ? claims.email.trim().toLocaleLowerCase("en-US")
      : "";
    if (claims.type !== "app" || !subject || !email) return null;

    const allowlist = ownerAllowlist(runtimeEnv);
    if (allowlist.subjects.size === 0 && allowlist.emails.size === 0) return null;
    if (!allowlist.subjects.has(subject) && !allowlist.emails.has(email)) return null;

    return { actor_id: `cloudflare-access:${subject}`, role: "OWNER" };
  } catch {
    return null;
  }
}
