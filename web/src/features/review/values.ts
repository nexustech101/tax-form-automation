/**
 * Kind-aware input for corrected values. The API parses and normalises every value ('1,234.50' -> 1234.5)
 * and answers `422 invalid_value` when it can't; this only catches obvious typos before the round trip.
 */
import type { FieldKind, Scalar } from "../../api/types";

export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];

const PATTERNS: Partial<Record<FieldKind, [RegExp, string]>> = {
  money: [/^(?=.*\d)\(?-?\$?\s*(\d{1,3}(,\d{3})+|\d+)?(\.\d{1,2})?\)?$/, "Enter an amount, e.g. 1,234.56"],
  percent: [/^-?\d+(\.\d+)?\s*%?$/, "Enter a percentage, e.g. 25.5"],
  tin: [/^([\dX*]{3}-?[\dX*]{2}-?[\dX*]{4}|[\dX*]{2}-?[\dX*]{7})$/i, "Enter 9 digits, e.g. 123-45-6789 or 12-3456789"],
  date: [/^\d{4}-\d{2}-\d{2}$/, "Enter a date"],
  acquired: [/^(various|\d{1,2}\/\d{1,2}\/\d{4}|\d{4}-\d{2}-\d{2})$/i, "Enter a date (MM/DD/YYYY) or VARIOUS"],
  state: [/^[A-Za-z]{2}$/, "Enter a 2-letter state code"],
  year: [/^(19|20)\d{2}$/, "Enter a 4-digit year"],
};

export type Parsed = { ok: true; value: Scalar } | { ok: false; error: string };

/** The string an editor starts with for a stored value. */
export function toInput(kind: FieldKind, value: Scalar): string {
  if (value === null) return "";
  if (kind === "money" && typeof value === "number") return value.toFixed(2);
  return String(value);
}

/** Editor text -> the value sent as `corrected`, or why it can't be. */
export function parseInput(kind: FieldKind, raw: string, options?: string[] | null): Parsed {
  const text = raw.trim();
  if (text === "") return { ok: false, error: "Enter a value, or reject the field when there is none" };
  if (kind === "check") {
    return text === "true" || text === "false" ? { ok: true, value: text === "true" } : { ok: false, error: "Choose checked or unchecked" };
  }
  if (kind === "choice") {
    return options?.includes(text) ? { ok: true, value: text } : { ok: false, error: "Choose one of the options" };
  }
  if (kind === "month") {
    return MONTHS.includes(text) ? { ok: true, value: text } : { ok: false, error: "Choose a month" };
  }
  const pattern = PATTERNS[kind];
  if (pattern && !pattern[0].test(text)) return { ok: false, error: pattern[1] };
  if (kind === "year") return { ok: true, value: Number(text) };
  if (kind === "state") return { ok: true, value: text.toUpperCase() };
  return { ok: true, value: text };
}
