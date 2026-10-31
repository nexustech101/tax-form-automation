/** Display of flag codes (core/flags.py) and review decisions: a short label and a tone each. */
import type { Decision, FlagCode } from "../api/types";

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

/** Removed values are `danger`, values to double-check `warning`, facts about the whole form `info`. */
export const FLAGS: Record<FlagCode, { label: string; tone: Tone }> = {
  not_printed: { label: "Not printed", tone: "danger" },
  wrong_label: { label: "Wrong label", tone: "danger" },
  unparseable: { label: "Unparseable", tone: "danger" },
  no_output: { label: "No output", tone: "danger" },
  unlabeled: { label: "Unlabeled", tone: "warning" },
  multiple_values: { label: "Multiple values", tone: "warning" },
  text_as_json: { label: "Text as JSON", tone: "warning" },
  inconsistent: { label: "Inconsistent", tone: "warning" },
  partial_conversion: { label: "Partial conversion", tone: "info" },
  missing_page: { label: "Missing page", tone: "info" },
  uncertain_type: { label: "Uncertain type", tone: "info" },
};

export const DECISIONS: Record<Decision, { label: string; tone: Tone }> = {
  confirmed: { label: "Confirmed", tone: "success" },
  corrected: { label: "Corrected", tone: "accent" },
  rejected: { label: "Rejected", tone: "danger" },
};
