import { notFound } from "next/navigation";

// Any unmatched /dashboard/... URL gets the 404 inside the sidebar shell.
export default function Page() {
  notFound();
}
