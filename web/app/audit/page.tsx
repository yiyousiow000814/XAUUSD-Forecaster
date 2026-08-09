import { redirect } from "next/navigation";

type PageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const AUDIT_VIEWS = new Set(["news", "evidence", "stories", "decisions", "league", "coverage"]);

export default async function LegacyAuditPage({ searchParams }: PageProps) {
  const query = await searchParams;
  const requested = Array.isArray(query.view) ? query.view[0] : query.view;
  const view = requested && AUDIT_VIEWS.has(requested) ? requested : "news";
  redirect(`/?room=audit&view=${view}`);
}
