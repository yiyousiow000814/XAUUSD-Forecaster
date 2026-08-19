import { redirect } from "next/navigation";

export default function RetryJobsPage() {
  redirect("/?room=retry");
}
