import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "./markdown";

describe("parseMarkdown", () => {
  it("splits paragraphs on blank lines and keeps single newlines", () => {
    expect(parseMarkdown("First line\nsecond line\n\nNext [1].")).toEqual([
      { kind: "p", inlines: [{ text: "First line\nsecond line" }] },
      { kind: "p", inlines: [{ text: "Next [1]." }] },
    ]);
  });

  it("groups bullet and numbered lists, with indented continuations", () => {
    expect(parseMarkdown("Open flags:\n- w2: 2\n  still open\n* stack: 6\n\n3. one\n4) two")).toEqual([
      { kind: "p", inlines: [{ text: "Open flags:" }] },
      { kind: "ul", items: [[{ text: "w2: 2 still open" }], [{ text: "stack: 6" }]] },
      { kind: "ol", start: 3, items: [[{ text: "one" }], [{ text: "two" }]] },
    ]);
  });

  it("reads headings and fenced code verbatim", () => {
    expect(parseMarkdown("## Totals\n```\n**not bold**\n```\nafter")).toEqual([
      { kind: "h", inlines: [{ text: "Totals" }] },
      { kind: "code", text: "**not bold**" },
      { kind: "p", inlines: [{ text: "after" }] },
    ]);
  });
});

describe("parseInline", () => {
  it("finds bold and code spans and leaves stray markers as text", () => {
    expect(parseInline("Wages **$68,412.55** in `box 1`, 2 * 3 ** x")).toEqual([
      { text: "Wages " },
      { text: "$68,412.55", bold: true },
      { text: " in " },
      { code: "box 1" },
      { text: ", 2 * 3 ** x" },
    ]);
  });

  it("does not interpret HTML", () => {
    expect(parseInline("<b>x</b>")).toEqual([{ text: "<b>x</b>" }]);
  });
});
