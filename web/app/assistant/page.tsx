import { redirect } from "next/navigation";

type PageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AssistantPage({ searchParams }: PageProps) {
  const requested = (await searchParams).returnTo;
  const returnTo = typeof requested === "string" ? requested : "";
  redirect(returnTo.startsWith("/?") ? returnTo : "/?room=assistant");
}
