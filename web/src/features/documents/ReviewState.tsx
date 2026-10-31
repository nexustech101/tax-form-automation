import { CheckCircle2 } from "lucide-react";
import type { LibraryDocument } from "../../api/types";
import { Badge } from "../../components/Badge";
import { plural } from "../../lib/format";

/** A document's review status in one badge: open flags first, then pending forms, then approved. */
export function ReviewState({ document: d }: { document: Pick<LibraryDocument, "review_status" | "open_flags" | "forms_pending"> }) {
  if (d.review_status === null) return <span className="muted">No forms</span>;
  if (d.review_status === "approved") return <Badge tone="success" icon={<CheckCircle2 />}>Approved</Badge>;
  if (d.open_flags > 0) return <Badge tone="warning" dot>{plural(d.open_flags, "open flag")}</Badge>;
  return <Badge tone="accent" dot>{plural(d.forms_pending, "form")} to approve</Badge>;
}
