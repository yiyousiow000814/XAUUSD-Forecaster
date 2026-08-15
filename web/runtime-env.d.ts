// Runtime bindings are configured outside source control and fail closed when absent.
declare global {
  namespace Cloudflare {
    interface Env {
      INGEST_TOKEN?: string;
      CF_ACCESS_TEAM_DOMAIN?: string;
      CF_ACCESS_AUD?: string;
      ASSISTANT_OWNER_SUBJECTS?: string;
      ASSISTANT_OWNER_EMAILS?: string;
    }
  }

  interface Env {
    INGEST_TOKEN?: string;
    CF_ACCESS_TEAM_DOMAIN?: string;
    CF_ACCESS_AUD?: string;
    ASSISTANT_OWNER_SUBJECTS?: string;
    ASSISTANT_OWNER_EMAILS?: string;
  }
}

export {};
