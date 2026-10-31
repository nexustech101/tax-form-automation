/**
 * The small Markdown subset assistant answers use, parsed into data (rendered as React elements, never as HTML):
 * paragraphs (single newlines kept), `#` headings, `-` / `*` / `1.` lists, fenced code, **bold** and `code`.
 */
export type Inline = { text: string; bold?: true } | { code: string };

export type Block =
  | { kind: "p" | "h"; inlines: Inline[] }
  | { kind: "ul"; items: Inline[][] }
  | { kind: "ol"; start: number; items: Inline[][] }
  | { kind: "code"; text: string };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;
const FENCE = /^\s*```/;

export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let paragraph: string[] = [];
  // (`as`: flush() resets it, which narrowing would not see)
  let list = null as { kind: "ul" | "ol"; start: number; items: string[] } | null;

  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "p", inlines: parseInline(paragraph.join("\n")) });
    paragraph = [];
    if (list) {
      const items = list.items.map(parseInline);
      blocks.push(list.kind === "ul" ? { kind: "ul", items } : { kind: "ol", start: list.start, items });
    }
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (FENCE.test(line)) {
      flush();
      const code: string[] = [];
      while (++i < lines.length && !FENCE.test(lines[i]!)) code.push(lines[i]!);
      blocks.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "h", inlines: parseInline(heading[1]!) });
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      const kind = bullet ? "ul" : "ol";
      if (paragraph.length || list?.kind !== kind) flush();
      list ??= { kind, start: numbered ? Number(numbered[1]) : 1, items: [] };
      list.items.push(bullet ? bullet[1]! : numbered![2]!);
      continue;
    }
    if (list && /^\s/.test(line)) {
      // An indented line continues the last list item.
      list.items[list.items.length - 1] += ` ${line.trim()}`;
      continue;
    }
    if (list) flush();
    paragraph.push(line);
  }
  flush();
  return blocks;
}

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(/\*\*(?=\S)([^*]+?)\*\*|`([^`\n]+)`/g)) {
    if (match.index > last) out.push({ text: text.slice(last, match.index) });
    out.push(match[1] !== undefined ? { text: match[1], bold: true } : { code: match[2]! });
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}
