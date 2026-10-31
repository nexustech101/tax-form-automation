import { describe, expect, it } from "vitest";
import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("turns known [n] into citations and keeps the rest as text", () => {
    expect(splitCitations("Wages were $68,412.55 [1]. See [2, 3]. Not [9].", new Set([1, 2, 3]))).toEqual([
      { text: "Wages were $68,412.55 " },
      { cite: 1 },
      { text: ". See " },
      { cite: 2 },
      { cite: 3 },
      { text: ". Not [9]." },
    ]);
  });

  it("returns plain text when nothing is cited", () => {
    expect(splitCitations("No sources.", new Set())).toEqual([{ text: "No sources." }]);
  });
});
