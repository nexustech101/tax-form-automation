/**
 * Source boxes (`ValueSource.box`) are [left, top, right, bottom] on a page 1000 units wide, origin top-left;
 * the vertical axis uses the same unit, so a page is 1000 * height / width units tall.
 */
import type { CSSProperties } from "react";

export type Box = [number, number, number, number];

/** Page height in box units. */
export function pageUnits(width: number, height: number): number {
  return (1000 * height) / width;
}

/** Position of a box over the rendered page, in percent of the page, so it follows any zoom. */
export function boxStyle(box: Box, units: number): CSSProperties {
  const [l, t, r, b] = box;
  return {
    left: `${l / 10}%`,
    top: `${(t / units) * 100}%`,
    width: `${(r - l) / 10}%`,
    height: `${((b - t) / units) * 100}%`,
  };
}

/**
 * A crop of the page around `box` (plus `pad` units, clamped to the page) as a background image sized to fit
 * `maxWidth` x `maxHeight` CSS pixels.
 */
export function cropStyle(box: Box, units: number, maxWidth: number, maxHeight: number, pad = 12): CSSProperties {
  const l = Math.max(0, box[0] - pad);
  const t = Math.max(0, box[1] - pad);
  const r = Math.min(1000, box[2] + pad);
  const b = Math.min(units, box[3] + pad);
  const scale = Math.min(maxWidth / (r - l), maxHeight / (b - t)); // CSS px per unit
  return {
    width: `${round((r - l) * scale)}px`,
    height: `${round((b - t) * scale)}px`,
    backgroundSize: `${round(1000 * scale)}px auto`,
    backgroundPosition: `${round(-l * scale)}px ${round(-t * scale)}px`,
  };
}

const round = (n: number) => Math.round(n * 100) / 100;
