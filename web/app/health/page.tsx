import { redirect } from "next/navigation";

export default function LegacyHealthPage() {
  redirect("/?room=health");
}
