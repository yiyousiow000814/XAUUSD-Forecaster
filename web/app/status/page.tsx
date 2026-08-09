import { redirect } from "next/navigation";

export default function LegacyStatusPage() {
  redirect("/?room=status");
}
