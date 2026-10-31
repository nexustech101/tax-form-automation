import { describe, expect, it } from "vitest";
import { boxStyle, cropStyle, pageUnits } from "./geometry";

describe("geometry", () => {
  const units = pageUnits(612, 792); // US Letter: 1294.1 units tall

  it("places boxes in percent of the page", () => {
    expect(units).toBeCloseTo(1294.12, 1);
    const style = boxStyle([100, 647.06, 300, 776.47], units);
    expect(style.left).toBe("10%");
    expect(style.width).toBe("20%");
    expect(parseFloat(String(style.top))).toBeCloseTo(50, 3);
    expect(parseFloat(String(style.height))).toBeCloseTo(10, 3);
  });

  it("crops around a box with padding, fitting the limits", () => {
    const style = cropStyle([200, 100, 300, 120], units, 200, 40, 10);
    // region 190..310 x 90..130 = 120 x 40 units; scale = min(200/120, 40/40) = 1
    expect(style).toEqual({ width: "120px", height: "40px", backgroundSize: "1000px auto", backgroundPosition: "-190px -90px" });
  });

  it("clamps the padding at the page edges", () => {
    const style = cropStyle([0, 0, 50, 10], units, 100, 100, 10);
    expect(style.backgroundPosition).toBe("0px 0px");
    expect(style.width).toBe("100px"); // 60 x 20 units, width-bound: scale 100/60
  });
});
