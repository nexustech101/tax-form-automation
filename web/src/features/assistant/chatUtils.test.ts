import { describe, expect, it } from "vitest";
import { describeTool, greeting, isNearBottom } from "./chatUtils";

describe("greeting", () => {
  it("follows the time of day", () => {
    expect([4, 5, 11, 12, 17, 18, 23].map(greeting)).toEqual([
      "Good evening",
      "Good morning",
      "Good morning",
      "Good afternoon",
      "Good afternoon",
      "Good evening",
      "Good evening",
    ]);
  });
});

describe("isNearBottom", () => {
  it("is true at the end or within the slack, false when scrolled up", () => {
    expect(isNearBottom({ scrollTop: 600, scrollHeight: 1000, clientHeight: 400 })).toBe(true);
    expect(isNearBottom({ scrollTop: 530, scrollHeight: 1000, clientHeight: 400 })).toBe(true);
    expect(isNearBottom({ scrollTop: 400, scrollHeight: 1000, clientHeight: 400 })).toBe(false);
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 300, clientHeight: 400 })).toBe(true);
  });
});

describe("describeTool", () => {
  const titles = new Map([["d1", "w2"]]);
  it("words each tool, with the document title when known", () => {
    expect(describeTool({ name: "search_documents", arguments: { query: " wages " } }, titles)).toBe("Searched “wages”");
    expect(describeTool({ name: "list_documents", arguments: {} }, titles)).toBe("Listed documents");
    expect(describeTool({ name: "get_document", arguments: { document_id: "d1" } }, titles)).toBe("Read w2");
    expect(describeTool({ name: "classify_document", arguments: { document_id: "x" } }, titles)).toBe("Classified a document");
    expect(describeTool({ name: "other", arguments: {} }, titles)).toBe("Ran other");
  });

  it("shortens long queries", () => {
    const label = describeTool({ name: "search_documents", arguments: { query: "a".repeat(80) } }, titles);
    expect(label.length).toBeLessThan(60);
    expect(label.endsWith("…”")).toBe(true);
  });
});
