import { describe, expect, it } from "vitest";
import { parseInput, toInput } from "./values";

describe("parseInput", () => {
  it("accepts amounts the API parses and rejects garbage", () => {
    for (const ok of ["1,234.56", "$72,500", "68412.55", "(12.00)", "-5", ".5"]) {
      expect(parseInput("money", ok)).toEqual({ ok: true, value: ok });
    }
    for (const bad of ["12a", "1,23,4", "1.2.3", "$"]) expect(parseInput("money", bad).ok).toBe(false);
  });

  it("types checkboxes, years and states", () => {
    expect(parseInput("check", "false")).toEqual({ ok: true, value: false });
    expect(parseInput("year", "2025")).toEqual({ ok: true, value: 2025 });
    expect(parseInput("state", "tx")).toEqual({ ok: true, value: "TX" });
    expect(parseInput("year", "25").ok).toBe(false);
  });

  it("checks TINs, choices and empty input", () => {
    expect(parseInput("tin", "123456789").ok).toBe(true);
    expect(parseInput("tin", "XXX-XX-6789").ok).toBe(true);
    expect(parseInput("tin", "12-34567").ok).toBe(false);
    expect(parseInput("choice", "B", ["A", "B"])).toEqual({ ok: true, value: "B" });
    expect(parseInput("choice", "C", ["A", "B"]).ok).toBe(false);
    expect(parseInput("text", "  ").ok).toBe(false);
  });
});

describe("toInput", () => {
  it("starts editors from the stored value", () => {
    expect(toInput("money", 72500)).toBe("72500.00");
    expect(toInput("check", true)).toBe("true");
    expect(toInput("text", null)).toBe("");
  });
});
