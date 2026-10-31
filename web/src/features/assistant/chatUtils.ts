/** Chat helpers without React: greeting, scroll stickiness, tool-call wording. */
import type { ChatResponse } from "../../api/types";

type ToolCall = ChatResponse["tool_calls"][number];

/** "Good morning" before 12:00, "Good afternoon" before 18:00, else "Good evening". */
export function greeting(hour: number): string {
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Whether a scroll container is at (or within `slack` px of) its end: new messages then keep it at the end. */
export function isNearBottom(el: { scrollTop: number; scrollHeight: number; clientHeight: number }, slack = 80): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= slack;
}

/** One tool call in words, e.g. `Searched “wages”`, `Read w2`; `titles` maps document ids to titles. */
export function describeTool(call: ToolCall, titles: Map<string, string>): string {
  const args = call.arguments as Record<string, unknown>;
  const id = typeof args.document_id === "string" ? args.document_id : null;
  const doc = id ? (titles.get(id) ?? "a document") : "";
  switch (call.name) {
    case "search_documents":
      return typeof args.query === "string" && args.query.trim() ? `Searched “${clip(args.query.trim())}”` : "Searched documents";
    case "list_documents":
      return "Listed documents";
    case "get_document":
      return `Read ${doc}`;
    case "classify_document":
      return `Classified ${doc}`;
    case "extract_document":
      return `Re-extracted ${doc}`;
    default:
      return `Ran ${call.name}`;
  }
}

function clip(text: string, max = 48): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}
