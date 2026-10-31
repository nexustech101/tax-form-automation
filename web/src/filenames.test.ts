import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

/** Module names (folder + name without .ts/.tsx) that are equal when case is ignored, as on Windows and macOS. */
function caseClashes(dir: string): string[] {
  const seen = new Map<string, string>();
  const clashes: string[] = [];
  for (const entry of readdirSync(dir, { recursive: true, encoding: "utf8" })) {
    const module = entry.replace(/\\/g, "/").replace(/\.tsx?$/, "");
    if (module === entry.replace(/\\/g, "/")) continue; // not a .ts/.tsx file
    const other = seen.get(module.toLowerCase());
    if (other !== undefined && other !== module) clashes.push(`${other} / ${module}`);
    seen.set(module.toLowerCase(), module);
  }
  return clashes;
}

it("no two modules differ only in case (./Chat would load chat.ts on Windows)", () => {
  expect(caseClashes(fileURLToPath(new URL(".", import.meta.url)))).toEqual([]);
});
