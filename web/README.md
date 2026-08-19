# Aurum Signal Room

The XAUUSD Forecaster public research dashboard. The same application can run
on ChatGPT Sites and on an independent Cloudflare Worker. Cloudflare stores the
latest public snapshots in D1, so public visitors never connect to the owner's
PC directly.

## Prerequisites

- Node.js `>=22.13.0`

## Quick Start

```bash
npm install
npm run dev
npm run build
```

## Cloudflare Shape

- vinext and the Cloudflare Vite plugin build the UI and API as one Worker.
- `worker/index.ts` is the Worker entry point.
- `wrangler.jsonc` declares D1 and runtime bindings.
- `drizzle/` contains the append-only dashboard migrations.
- the local synchronizer writes Sites and Cloudflare independently.
- `INGEST_TOKEN` is a required generated binding. Production-only relay and
  Access values remain optional, fail-closed Cloudflare bindings so isolated
  branch Previews can deploy without production authority.

## Workspace Auth Headers

Signed-in visitors receive both `oai-authenticated-user-id` and `oai-authenticated-user-email`. Private Sites require every visitor to sign in; public Sites may also have anonymous visitors, for whom neither header is present.

The user ID is stable for the same user on the same Site and different across Sites. Email and name are intended for display or contact purposes.

SIWC-authenticated workspace sites may also receive
`oai-authenticated-user-full-name` when the user's SIWC profile has a non-empty
`name` claim. The full-name value is percent-encoded UTF-8 and is accompanied by
`oai-authenticated-user-full-name-encoding: percent-encoded-utf-8`.

Treat the full name as optional and fall back to email when it is absent:

```tsx
import { headers } from "next/headers";

export default async function Home() {
  const requestHeaders = await headers();
  const userId = requestHeaders.get("oai-authenticated-user-id");
  const email = requestHeaders.get("oai-authenticated-user-email");
  const encodedFullName = requestHeaders.get("oai-authenticated-user-full-name");
  const fullName =
    encodedFullName &&
    requestHeaders.get("oai-authenticated-user-full-name-encoding") ===
      "percent-encoded-utf-8"
      ? decodeURIComponent(encodedFullName)
      : null;

  const displayName = fullName ?? email;
  // ...
}
```

## Optional Dispatch-Owned ChatGPT Sign-In

Import the ready-to-use helpers from `app/chatgpt-auth.ts` when the site needs
optional or required ChatGPT sign-in:

- Use `getChatGPTUser()` for optional signed-in UI.
- Use `requireChatGPTUser(returnTo)` for server-rendered pages that should send
  anonymous visitors through Sign in with ChatGPT.
- Use `chatGPTSignInPath(returnTo)` and `chatGPTSignOutPath(returnTo)` for
  browser links or actions.
- Pass a same-origin relative `returnTo` path for the destination after sign-in
  or sign-out. The helper validates and safely encodes it.
- Mark protected pages with `export const dynamic = "force-dynamic"` because
  they depend on per-request identity headers.

Dispatch owns `/signin-with-chatgpt`, `/signout-with-chatgpt`, `/callback`, the
OAuth cookies, and identity header injection. Do not implement app routes for
those reserved paths. Routes that do not import and call the helper remain
anonymous-compatible.

SIWC establishes identity only; it does not prove workspace membership. Use the
Sites hosting platform's access policy controls for workspace-wide restrictions,
or enforce explicit server-side membership or allowlist checks.

Use SIWC for account pages, user-specific dashboards, saved records, and write
actions tied to the current ChatGPT user. Leave public content anonymous.

## Useful Commands

- `npm run dev`: start local development
- `npm run build`: verify the vinext build output
- `npm test`: build the application and run rendered API/UI regressions
- `npm run lint`: lint source code while excluding generated hosting artifacts
- `npm run db:generate`: generate Drizzle migrations after schema changes
- `npm run cf:types`: refresh Cloudflare binding types
- `npm run cf:types:check`: fail when committed binding types drift from config
- `npm run cf:deploy`: test and deploy the Worker

## One-time Cloudflare Setup

```powershell
npx wrangler login
npx wrangler d1 migrations apply aurum-signal-room --remote
npx wrangler secret put INGEST_TOKEN
npx wrangler secret put STATUS_RELAY_URL
npx wrangler secret put CF_ACCESS_TEAM_DOMAIN
npx wrangler secret put CF_ACCESS_AUD
npx wrangler secret put DASHBOARD_OPERATOR_OWNER_SUBJECTS
npx wrangler secret put DASHBOARD_OPERATOR_OWNER_EMAILS
npm run cf:deploy
```

One Cloudflare Access application and at least one matching Dashboard Operator
owner subject or email must be configured before the Admin Console or its human
APIs are enabled. Both owner allowlist names remain explicit runtime
contracts; set an unused allowlist to a
non-matching sentinel value rather than placing owner identity in source. These
production-only values are intentionally not `secrets.required`: branch Preview
versions have no model authority and must remain deployable without them.
Activating a Cloudflare Zero Trust plan and configuring the Google IdP are
account-level prerequisites; deploying the Worker and setting secrets do not
create the outer Access boundary.

Protect `/admin*` (including the canonical `/admin/api/*` browser APIs) and the
compatibility routes `/assistant`, `/retry-jobs`, and `/status` with one Access
application. The root `/api/*` handler URLs remain fail-closed compatibility
surfaces; the Admin browser uses only `/admin/api/*` so public research APIs and
machine endpoints stay outside Access.
Keep `/api/assistant-worker/*` and `/api/operator-retry-worker` outside that
application; they are non-browser control planes and accept only `INGEST_TOKEN`
plus the applicable job lease.

`scripts/check_public_health.py` intentionally checks only anonymous public
surfaces. Use `scripts/check_admin_access_boundary.py` for the anonymous Access
redirect and machine-route boundary, then verify private Assistant health in an
authenticated Admin browser session.

The local Dashboard API and Dashboard Mirrors processes also require the same
user-level `DASHBOARD_OPERATOR_BRIDGE_TOKEN` with at least 32 characters. It is
a dedicated localhost machine credential, not a Wrangler secret, Access token,
or replacement for `INGEST_TOKEN`; see the Cloudflare deployment runbook.

The local Control Center reads these user-level environment variables when it
starts `Dashboard Mirrors`:

- `CLOUDFLARE_INGEST_URL`
- `CLOUDFLARE_INGEST_TOKEN`
- `DASHBOARD_OPERATOR_BRIDGE_TOKEN`
- `XAUUSD_DASHBOARD_URL`

For automatic deployment after a GitHub push, connect the GitHub repository in
Cloudflare Workers Builds, set the root directory to `web`, use
`npm ci && npm test` as the build command and
`npx wrangler deploy` as the deploy command. Keep D1 identifiers in
`wrangler.jsonc`; keep `INGEST_TOKEN` in Cloudflare secrets.

## Learn More

- [vinext Documentation](https://github.com/cloudflare/vinext)
- [Drizzle D1 Guide](https://orm.drizzle.team/docs/get-started/d1-new)
- [Cloudflare Workers Builds](https://developers.cloudflare.com/workers/ci-cd/builds/)
- [Cloudflare D1](https://developers.cloudflare.com/d1/)
