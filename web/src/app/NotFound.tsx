import { FileQuestion } from "lucide-react";
import { Link } from "react-router";
import { EmptyState } from "../components/EmptyState";

export function NotFound() {
  return (
    <EmptyState icon={<FileQuestion />} title="Page not found" action={<Link to="/">Go to the overview</Link>}>
      The address doesn't match any page of the dashboard.
    </EmptyState>
  );
}
