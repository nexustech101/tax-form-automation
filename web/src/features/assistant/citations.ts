/** Splits an answer into text and `[n]` citations (also `[1, 2]` and `[1][2]`), keeping only cited numbers that exist. */
export type Part = { text: string } | { cite: number };

export function splitCitations(answer: string, known: Set<number>): Part[] {
  const parts: Part[] = [];
  let last = 0;
  for (const match of answer.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
    const numbers = match[1]!.split(",").map((n) => Number(n.trim()));
    if (!numbers.every((n) => known.has(n))) continue;
    if (match.index > last) parts.push({ text: answer.slice(last, match.index) });
    for (const n of numbers) parts.push({ cite: n });
    last = match.index + match[0].length;
  }
  if (last < answer.length) parts.push({ text: answer.slice(last) });
  return parts;
}
