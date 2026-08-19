// Production-only bindings may be absent from isolated branch Previews.
// Their consumers must fail closed; values remain Cloudflare secrets/variables.
declare global {
  namespace Cloudflare {
    interface Env {
      STATUS_RELAY_URL?: string;
      CF_ACCESS_TEAM_DOMAIN?: string;
      CF_ACCESS_AUD?: string;
      DASHBOARD_OPERATOR_OWNER_SUBJECTS?: string;
      DASHBOARD_OPERATOR_OWNER_EMAILS?: string;
      ASSISTANT_OWNER_SUBJECTS?: string;
      ASSISTANT_OWNER_EMAILS?: string;
    }
  }

  interface Env {
    STATUS_RELAY_URL?: string;
    CF_ACCESS_TEAM_DOMAIN?: string;
    CF_ACCESS_AUD?: string;
    DASHBOARD_OPERATOR_OWNER_SUBJECTS?: string;
    DASHBOARD_OPERATOR_OWNER_EMAILS?: string;
    ASSISTANT_OWNER_SUBJECTS?: string;
    ASSISTANT_OWNER_EMAILS?: string;
  }
}

export {};
